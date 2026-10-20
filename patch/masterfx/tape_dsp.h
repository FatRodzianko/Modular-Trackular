/*
 * tape_dsp.h: a tape echo on the master delay object (Polyend Tracker+ firmware 1.2.0),
 * for masterfx.c. Include it once, in one .c file, after freeverb_dsp.h.
 *
 * The master delay is one audio object (0x20018fc0) in the firmware's Teensy-style audio
 * graph. Its update() (vtable 0x60042fec, 0x60032405) takes the mono delay send (one
 * input), allocates two blocks and transmits them as the stereo delay return (wet only).
 * tape_blocks() does the same with a model of a tape echo in the style of a Roland Space
 * Echo: one record head and one playback head on a loop of tape, the playback head's
 * signal fed back to the record head.
 *
 *  - Tape: the stock delay's own buffers (three 2^18-sample 16-bit buffers at object
 *    +0x14, +0x18, +0x1c, about 5.9 s each): tape A in the first, tape B in the second.
 *  - Time: the stock field +0x28 (delay in samples, from Time or from Sync and Rate, up
 *    to 3500 ms) is the tape speed. When it changes the playback point glides there (time
 *    constant about 0.15 s; at most 0.9 samples per sample longer, 3 shorter), so the
 *    echoes bend in pitch as on a tape machine.
 *  - Wow, flutter and drift: three slow sine modulations of the playback point (0.55 Hz,
 *    6.3 Hz and 0.13 Hz; about 0.35 %, 0.18 % and 0.1 % of pitch, or 6, 3 and 1.7
 *    cents), interpolated across the block. The playback head reads between samples
 *    (4-point Hermite interpolation).
 *  - Intensity: the stock Feedback byte (+0x2c, 0..252) sets the loop gain, up to 1.04
 *    at the top, so the echoes can build up and sustain as on a Space Echo; the record
 *    head's saturation keeps them from growing without limit.
 *  - Tone: the stock Filter's one-pole lowpass (its pole, from the Q10 coefficient at
 *    +0x38) on the playback head, so every repeat, the first one included, is filtered
 *    as with the stock delay; the tape's own high-frequency loss, a one-pole lowpass at
 *    7 kHz on the record head, so every pass round the loop is a little darker; and a
 *    70 Hz low cut in the feedback, so repeats get thinner as they go round.
 *  - Saturation: the record head is driven 1.7 times into a soft clip (u - 4u^3/27, flat
 *    from 1.5 on) and scaled back, so ordinary send levels pick up a little harmonic
 *    warmth, and loud sends and high Intensity compress instead of clipping hard.
 *  - PingPong (+0x2e): off, tape A only (also written to tape B), the same echo on both
 *    sides, like the mono original; on, the two tapes feed each other, so the repeats
 *    alternate right and left (the first on the right), as the stock delay's do.
 *  - Bypass (+0x2d) as the stock delay: the send is received and released, nothing sent.
 *
 * The state (struct tape_state) is in .mt_ram, placed by the patcher.
 */
#ifndef TAPE_DSP_H
#define TAPE_DSP_H

#include <stdint.h>

/* ---- firmware 1.2.0 (function pointers carry the Thumb bit) ---------------------- */
#define FW_RECEIVE_READONLY 0x600305BDu     /* (object, input) -> block or 0 */
#define FW_ALLOCATE         0x60030479u     /* () -> block or 0 */
#define FW_DELAY_UPDATE     0x60032405u     /* the stock delay update() */

/* delay object fields */
#define DL_BUF0             0x14u           /* -> int16[2^18], SDRAM, allocated at runtime */
#define DL_BUF1             0x18u
#define DL_BUF2             0x1Cu
#define DL_TIME             0x28u           /* uint32: delay in samples (Time, or Sync + Rate) */
#define DL_FEEDBACK         0x2Cu           /* byte: Feedback x 255, 0..252 */
#define DL_BYPASS           0x2Du           /* byte: nonzero = no processing, no output */
#define DL_PINGPONG         0x2Eu           /* byte */
#define DL_FILTER_POLE      0x38u           /* int32: -pole x 1023 (Filter, a one-pole lowpass) */
#define DL_STATE_FIRST      0x3Cu           /* the stock delay's filter states, +0x3c..+0x50 */
#define DL_STATE_LAST       0x50u

#define TAPE_LEN            (1u << 18)      /* each stock buffer: 2^18 samples */
#define TAPE_MASK           (TAPE_LEN - 1u)
#define TAPE_MIN            8u              /* shortest delay, samples */
#define TAPE_MAX            (TAPE_LEN - 512u)

typedef void *(*allocate_fn)(void);

/* ---- tape tuning ------------------------------------------------------------------ */
#define GLIDE               0.00015f        /* per sample towards the new Time (about 0.15 s) */
#define GLIDE_RATE          0.9f            /* most growth of the delay per sample (the pitch dips) */
#define GLIDE_FAST_TAPE     3.0f            /* most shrinking per sample (the pitch rises), as the motor speeds up */
#define INTENSITY_MAX       1.04f           /* loop gain at Feedback 0.99 (byte 252) */
#define LOWCUT              0.99008f        /* one-pole low cut in the feedback: exp(-2 pi 70 / 44100) */
#define TAPE_HF             0.63114f        /* the record head's lowpass: 1 - exp(-2 pi 7000 / 44100) */
#define DRIVE               1.7f            /* into the saturation, and back out */
#define POLE_MIN            (-0.99f)
#define POLE_MAX            0.995f
#define SAT_KNEE            1.5f            /* the record head's saturation is flat from here */

/* the three modulations of the playback point: depth in samples, and the rotation of
 * each oscillator over one block (cos, sin of 2 pi f 128 / 44100) */
#define MODS                3u
static const float mod_depth[MODS] = {45.0f, 2.0f, 54.0f};          /* wow, flutter, drift */
static const float mod_rot[MODS][2] = {
    {0.99994970f, 0.01003013f},             /* 0.55 Hz */
    {0.99340711f, 0.11463993f},             /* 6.3 Hz */
    {0.99999719f, 0.00237080f},             /* 0.13 Hz */
};

/* ---- state (.mt_ram, placed by the patcher in on-chip RAM) ----------------------- */
#define TAPE_MAGIC          0x45504154u     /* "TAPE": laid out for buf[] */

struct tape_state {
    uint32_t magic;
    uint32_t buf[2];                        /* tape A and tape B: the stock buffers 0 and 1 */
    uint32_t wpos;                          /* record position, 0..TAPE_LEN-1 */
    uint32_t dint;                          /* the playback delay: dint + dfrac samples */
    float dfrac;                            /* 0..1 */
    float osc[MODS][2];                     /* cos, sin */
    float lp_x[2], lp_y[2];                 /* tone lowpass, per tape */
    float hp_x[2], hp_y[2];                 /* low cut, per tape */
    float rec_lp[2];                        /* the record head's lowpass, per tape */
};
static struct tape_state tape_state_ram __attribute__((section(".mt_ram")));
#define TP (&tape_state_ram)

static inline uint32_t tape_target(const uint8_t *obj)
{
    uint32_t t = *(volatile uint32_t *)(obj + DL_TIME);
    if (t < TAPE_MIN)
        t = TAPE_MIN;
    if (t > TAPE_MAX)
        t = TAPE_MAX;
    return t;
}

/* Lay out the tapes in buffers 0 and 1 (cleared beforehand); the delay starts at the
 * current Time, without a glide. */
__attribute__((unused)) static void tape_init(const uint8_t *obj, uint32_t buf0, uint32_t buf1)
{
    TP->buf[0] = buf0;
    TP->buf[1] = buf1;
    TP->wpos = 0;
    TP->dint = tape_target(obj);
    TP->dfrac = 0.0f;
    for (uint32_t k = 0; k < MODS; k++) {
        TP->osc[k][0] = 1.0f;
        TP->osc[k][1] = 0.0f;
    }
    for (uint32_t k = 0; k < 2; k++)
        TP->lp_x[k] = TP->lp_y[k] = TP->hp_x[k] = TP->hp_y[k] = TP->rec_lp[k] = 0.0f;
    TP->magic = TAPE_MAGIC;
}

/* One sample of the delay moving towards the target: a part `coef` of the way, growing
 * by at most GLIDE_RATE and shrinking by at most `fast` samples per sample. */
static inline void glide(uint32_t target, uint32_t *dint, float *dfrac, float coef, float fast)
{
    float step = ((float)((int32_t)target - (int32_t)*dint) - *dfrac) * coef;
    if (step > GLIDE_RATE)
        step = GLIDE_RATE;
    if (step < -fast)
        step = -fast;
    float f = *dfrac + step;
    int32_t whole = (int32_t)f;
    if (f < (float)whole)
        whole--;                            /* floor */
    *dint = (uint32_t)((int32_t)*dint + whole);
    *dfrac = f - (float)whole;
}

static inline float tape_sat(float u)       /* the record head */
{
    if (u >= SAT_KNEE)
        return 1.0f;
    if (u <= -SAT_KNEE)
        return -1.0f;
    return u - (4.0f / 27.0f) * u * u * u;
}

/* The record head: the tape's high-frequency loss, then the drive into the saturation. */
static inline float tape_record(float v, float *lp)
{
    *lp += TAPE_HF * (v - *lp);
    return tape_sat(DRIVE * *lp) * (1.0f / DRIVE);
}

static inline int16_t to16(float u)         /* -1..1 -> 16 bits */
{
    int32_t v = (int32_t)(u * 32767.0f);
    return (int16_t)(v > 32767 ? 32767 : v < -32767 ? -32767 : v);
}

/* The playback head: tape q, `back` whole samples behind the record position, plus
 * `frac` (0..1) further back; 4-point Hermite interpolation. */
static inline float tape_read(const int16_t *q, uint32_t pos, uint32_t back, float frac)
{
    uint32_t i = (pos - back - 1u) & TAPE_MASK;     /* the sample just before the point */
    float u = 1.0f - frac;                          /* the point, from sample i */
    float xm1 = q[(i - 1u) & TAPE_MASK], x0 = q[i], x1 = q[(i + 1u) & TAPE_MASK], x2 = q[(i + 2u) & TAPE_MASK];
    float c1 = 0.5f * (x1 - xm1);
    float c2 = xm1 - 2.5f * x0 + 2.0f * x1 - 0.5f * x2;
    float c3 = 0.5f * (x2 - xm1) + 1.5f * (x0 - x1);
    return (((c3 * u + c2) * u + c1) * u + x0) * (1.0f / 32768.0f);
}

/* Run the tape over one block: mono send in, left and right out. */
static void tape_process(const uint8_t *obj, const int16_t *in, int16_t *out_l, int16_t *out_r)
{
    int16_t *qa = (int16_t *)TP->buf[0], *qb = (int16_t *)TP->buf[1];
    int pingpong = *(volatile uint8_t *)(obj + DL_PINGPONG) != 0;
    float gain = (float)*(volatile uint8_t *)(obj + DL_FEEDBACK) * (INTENSITY_MAX / 252.0f);
    if (gain > INTENSITY_MAX)
        gain = INTENSITY_MAX;
    float pole = -(float)*(volatile int32_t *)(obj + DL_FILTER_POLE) * (1.0f / 1023.0f);
    if (!(pole >= POLE_MIN))
        pole = POLE_MIN;
    if (pole > POLE_MAX)
        pole = POLE_MAX;
    float lp_b = 0.5f * (1.0f - pole);
    uint32_t target = tape_target(obj);

    /* the modulation at the start and the end of the block, interpolated in between */
    float m0 = 0.0f, m1 = 0.0f;
    for (uint32_t k = 0; k < MODS; k++) {
        float c = TP->osc[k][0], s = TP->osc[k][1];
        m0 += mod_depth[k] * s;
        float c2 = c * mod_rot[k][0] - s * mod_rot[k][1];
        float s2 = s * mod_rot[k][0] + c * mod_rot[k][1];
        float g = 1.5f - 0.5f * (c2 * c2 + s2 * s2);    /* keep the oscillator on the circle */
        TP->osc[k][0] = c2 * g;
        TP->osc[k][1] = s2 * g;
        m1 += mod_depth[k] * TP->osc[k][1];
    }
    float m_step = (m1 - m0) * (1.0f / BLOCK);

    uint32_t pos = TP->wpos, dint = TP->dint;
    float dfrac = TP->dfrac, m = m0;
    float lpx0 = TP->lp_x[0], lpy0 = TP->lp_y[0], lpx1 = TP->lp_x[1], lpy1 = TP->lp_y[1];
    float hpx0 = TP->hp_x[0], hpy0 = TP->hp_y[0], hpx1 = TP->hp_x[1], hpy1 = TP->hp_y[1];
    float rl0 = TP->rec_lp[0], rl1 = TP->rec_lp[1];
    for (uint32_t n = 0; n < BLOCK; n++) {
        /* the delay glides to the target */
        glide(target, &dint, &dfrac, GLIDE, GLIDE_FAST_TAPE);
        /* the playback point, modulated; never closer than TAPE_MIN / 2 to the record head */
        float f = dfrac + m;
        int32_t whole = (int32_t)f;
        if (f < (float)whole)
            whole--;                        /* floor */
        float frac = f - (float)whole;
        int32_t back = (int32_t)dint + whole;
        if (back < (int32_t)(TAPE_MIN / 2u)) {
            back = TAPE_MIN / 2u;
            frac = 0.0f;
        }
        m += m_step;

        float x = (float)in[n] * (1.0f / 32768.0f);
        float ya = tape_read(qa, pos, (uint32_t)back, frac);
        lpy0 = lp_b * (ya + lpx0) + pole * lpy0;    /* tone */
        lpx0 = ya;
        ya = lpy0;
        if (pingpong) {
            float yb = tape_read(qb, pos, (uint32_t)back, frac);
            lpy1 = lp_b * (yb + lpx1) + pole * lpy1;
            lpx1 = yb;
            yb = lpy1;
            hpy0 = LOWCUT * (hpy0 + yb - hpx0);     /* B's repeat, thinned, back onto A */
            hpx0 = yb;
            hpy1 = LOWCUT * (hpy1 + ya - hpx1);     /* A's repeat onto B */
            hpx1 = ya;
            qa[pos] = to16(tape_record(x + gain * hpy0, &rl0));
            qb[pos] = to16(tape_record(gain * hpy1, &rl1));
            out_l[n] = to16(yb);
            out_r[n] = to16(ya);
        } else {
            hpy0 = LOWCUT * (hpy0 + ya - hpx0);
            hpx0 = ya;
            int16_t rec = to16(tape_record(x + gain * hpy0, &rl0));
            qa[pos] = rec;
            qb[pos] = rec;                  /* so PingPong on carries on from the same tape */
            out_l[n] = out_r[n] = to16(ya);
        }
        pos = (pos + 1u) & TAPE_MASK;
    }
    TP->wpos = pos;
    TP->dint = dint;
    TP->dfrac = dfrac;
    TP->lp_x[0] = lpx0;
    TP->lp_y[0] = lpy0;
    TP->lp_x[1] = lpx1;
    TP->lp_y[1] = lpy1;
    TP->hp_x[0] = hpx0;
    TP->hp_y[0] = hpy0;
    TP->hp_x[1] = hpx1;
    TP->hp_y[1] = hpy1;
    TP->rec_lp[0] = rl0;
    TP->rec_lp[1] = rl1;
}

/* Take the send, run the tape, send the two return blocks, as the stock update() does
 * (a missing send counts as silence, so the echoes carry on). */
__attribute__((unused)) static void tape_blocks(uint8_t *obj)
{
    static const int16_t silence[BLOCK] = {0};
    uint8_t *in = ((receive_fn)FW_RECEIVE_READONLY)(obj, 0);
    uint8_t *left = ((allocate_fn)FW_ALLOCATE)();
    uint8_t *right = ((allocate_fn)FW_ALLOCATE)();
    if (left && right && !obj[DL_BYPASS]) {
        tape_process(obj, in ? (const int16_t *)(in + 4) : silence, (int16_t *)(left + 4), (int16_t *)(right + 4));
        ((transmit_fn)FW_TRANSMIT)(obj, left, 0);
        ((transmit_fn)FW_TRANSMIT)(obj, right, 1);
    }
    if (left)
        ((release_fn)FW_RELEASE)(left);
    if (right)
        ((release_fn)FW_RELEASE)(right);
    if (in)
        ((release_fn)FW_RELEASE)(in);
}

#endif
