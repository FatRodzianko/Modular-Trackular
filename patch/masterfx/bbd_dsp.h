/*
 * bbd_dsp.h: an analog bucket-brigade (BBD) delay on the master delay object (Polyend
 * Tracker+ firmware 1.2.0), for masterfx.c. Include it once, in one .c file, after
 * tape_dsp.h (it uses its field names, block helpers and playback head).
 *
 * A model of a delay built on a 4096-stage BBD chip (as the MN3005 in pedals such as
 * the Boss DM-2): the chip passes samples from stage to stage at a clock rate set by the
 * delay time, clock = 4096 / (2 x time), so long delays run a slow clock. Around it, an
 * anti-alias lowpass before the chip and a reconstruction lowpass after it, both at
 * about a third of the clock (2-pole Butterworth): about 7 kHz at 100 ms, 2.4 kHz at
 * 300 ms, 600 Hz at the longest. So the longer the delay, the darker the echoes, and
 * the chip's sampling at its clock (a sample and hold) leaves a little grit.
 *
 *  - Line: the stock delay's buffers (as the tape's): line A in the first, line B in
 *    the second for PingPong; the playback reads between samples (as the tape's head).
 *  - Time: the stock field +0x28 (from Time, or from Sync and Rate) sets the clock; a
 *    change sweeps there in about 45 ms (the clock changing; at most 0.9 samples per
 *    sample longer, 8 shorter), bending the pitch briefly. A slow drift (0.3 Hz, 1.5 samples) keeps it alive.
 *  - Feedback (+0x2c): loop gain up to 1.02 at the top, so the echoes can sustain; the
 *    chip's input clips softly (driven 1.2 times into u - 4u^3/27) and a 40 Hz low cut
 *    in the feedback keeps them from drifting.
 *  - Filter: the stock delay's one-pole lowpass on every echo, as for the stock delay.
 *  - PingPong (+0x2e) and the bypass flag (+0x2d) as for the tape.
 *
 * The state (struct bbd_state) is in .mt_ram, placed by the patcher.
 */
#ifndef BBD_DSP_H
#define BBD_DSP_H

#include <stdint.h>

#define BBD_CLOCK_K         31610880.0f     /* 0.35 x 4096 x 44100 / 2: the filters' corner = this / delay */
#define BBD_HOLD_K          2048.0f         /* clock / 44100 = this / delay (samples) */
#define BBD_FC_MIN          600.0f
#define BBD_FC_MAX          12000.0f
#define BBD_GLIDE           0.0005f         /* per sample towards the new Time (about 45 ms) */
#define BBD_GLIDE_FAST      8.0f            /* most shrinking of the delay per sample: a quick zip up */
#define BBD_INTENSITY_MAX   1.02f
#define BBD_DRIVE           1.2f
#define BBD_LOWCUT          0.99432f        /* exp(-2 pi 40 / 44100) */
#define BBD_DRIFT           1.5f            /* samples */
#define BBD_ROT_C           0.99998503f     /* per block: cos, sin of 2 pi 0.3 Hz 128 / 44100 */
#define BBD_ROT_S           0.00547105f

#define BBD_MAGIC           0x44444242u     /* "BBDD": laid out for buf[] */

struct biquad { float s1, s2; };

struct bbd_state {
    uint32_t magic;
    uint32_t buf[2];                        /* line A and line B: the stock buffers 0 and 1 */
    uint32_t wpos;
    uint32_t dint;                          /* the delay: dint + dfrac samples */
    float dfrac;
    uint32_t coef_for;                      /* the delay the filter coefficients are for */
    float b0, a1, a2;                       /* the lowpass: b1 = 2 b0, b2 = b0 */
    struct biquad aa[2], rc[2];             /* anti-alias and reconstruction, per line */
    float held[2];                          /* the chip's input, held between clock ticks */
    float phase;                            /* clock phase, 0..1 */
    float hp_x[2], hp_y[2];                 /* the feedback low cut */
    float lp_x[2], lp_y[2];                 /* the Filter knob's lowpass */
    float osc[2];                           /* cos, sin of the drift */
};
static struct bbd_state bbd_state_ram __attribute__((section(".mt_ram")));
#define BB (&bbd_state_ram)

static inline float bbd_tan(float x)        /* tan for 0 <= x <= 0.9 (within 2 %) */
{
    float x2 = x * x;
    return x * (1.0f + x2 * (1.0f / 3.0f + x2 * (2.0f / 15.0f)));
}

/* the 2-pole Butterworth lowpass at the corner for this delay */
static void bbd_coefs(uint32_t delay)
{
    float fc = BBD_CLOCK_K / (float)delay;
    if (fc < BBD_FC_MIN)
        fc = BBD_FC_MIN;
    if (fc > BBD_FC_MAX)
        fc = BBD_FC_MAX;
    float k = bbd_tan(3.14159265f * fc / 44100.0f), k2 = k * k;
    float norm = 1.0f / (1.0f + 1.41421356f * k + k2);
    BB->b0 = k2 * norm;
    BB->a1 = 2.0f * (k2 - 1.0f) * norm;
    BB->a2 = (1.0f - 1.41421356f * k + k2) * norm;
    BB->coef_for = delay;
}

static inline float bbd_lowpass(struct biquad *f, float x)
{
    float y = BB->b0 * x + f->s1;
    f->s1 = 2.0f * BB->b0 * x - BB->a1 * y + f->s2;
    f->s2 = BB->b0 * x - BB->a2 * y;
    return y;
}

/* Lay out the lines in buffers 0 and 1 (cleared beforehand), at the current Time. */
__attribute__((unused)) static void bbd_init(const uint8_t *obj, uint32_t buf0, uint32_t buf1)
{
    BB->buf[0] = buf0;
    BB->buf[1] = buf1;
    BB->wpos = 0;
    BB->dint = tape_target(obj);
    BB->dfrac = 0.0f;
    bbd_coefs(BB->dint);
    for (uint32_t k = 0; k < 2; k++) {
        BB->aa[k].s1 = BB->aa[k].s2 = BB->rc[k].s1 = BB->rc[k].s2 = 0.0f;
        BB->held[k] = BB->hp_x[k] = BB->hp_y[k] = BB->lp_x[k] = BB->lp_y[k] = 0.0f;
    }
    BB->phase = 0.0f;
    BB->osc[0] = 1.0f;
    BB->osc[1] = 0.0f;
    BB->magic = BBD_MAGIC;
}

static inline float bbd_chip_in(float v)    /* the chip's input stage */
{
    return tape_sat(BBD_DRIVE * v) * (1.0f / BBD_DRIVE);
}

/* Run the BBD over one block: mono send in, left and right out. */
static void bbd_process(const uint8_t *obj, const int16_t *in, int16_t *out_l, int16_t *out_r)
{
    int16_t *qa = (int16_t *)BB->buf[0], *qb = (int16_t *)BB->buf[1];
    int pingpong = *(volatile uint8_t *)(obj + DL_PINGPONG) != 0;
    float gain = (float)*(volatile uint8_t *)(obj + DL_FEEDBACK) * (BBD_INTENSITY_MAX / 252.0f);
    if (gain > BBD_INTENSITY_MAX)
        gain = BBD_INTENSITY_MAX;
    float pole = -(float)*(volatile int32_t *)(obj + DL_FILTER_POLE) * (1.0f / 1023.0f);
    if (!(pole >= POLE_MIN))
        pole = POLE_MIN;
    if (pole > POLE_MAX)
        pole = POLE_MAX;
    float lp_b = 0.5f * (1.0f - pole);
    uint32_t target = tape_target(obj);
    /* the filters follow the delay as it is now (they change once per block) */
    if (BB->dint != BB->coef_for)
        bbd_coefs(BB->dint);
    float hold = BBD_HOLD_K / (float)BB->dint;

    /* the drift, interpolated across the block */
    float c = BB->osc[0], s = BB->osc[1];
    float c2 = c * BBD_ROT_C - s * BBD_ROT_S, s2 = s * BBD_ROT_C + c * BBD_ROT_S;
    float g = 1.5f - 0.5f * (c2 * c2 + s2 * s2);
    BB->osc[0] = c2 * g;
    BB->osc[1] = s2 * g;
    float m = BBD_DRIFT * s, m_step = BBD_DRIFT * (BB->osc[1] - s) * (1.0f / BLOCK);

    uint32_t pos = BB->wpos, dint = BB->dint;
    float dfrac = BB->dfrac, phase = BB->phase;
    float held0 = BB->held[0], held1 = BB->held[1];
    for (uint32_t n = 0; n < BLOCK; n++) {
        /* the delay sweeps to the target, as the clock changes */
        glide(target, &dint, &dfrac, BBD_GLIDE, BBD_GLIDE_FAST);
        float f = dfrac + m;
        int32_t whole = (int32_t)f;
        if (f < (float)whole)
            whole--;
        float frac = f - (float)whole;
        int32_t back = (int32_t)dint + whole;
        if (back < (int32_t)(TAPE_MIN / 2u)) {
            back = TAPE_MIN / 2u;
            frac = 0.0f;
        }
        m += m_step;
        /* the chip's clock: a new sample is taken in at each tick */
        phase += hold;
        int tick = phase >= 1.0f;
        if (tick)
            phase -= (float)(int32_t)phase;

        float x = (float)in[n] * (1.0f / 32768.0f);
        float ya = bbd_lowpass(&BB->rc[0], tape_read(qa, pos, (uint32_t)back, frac));
        BB->lp_y[0] = lp_b * (ya + BB->lp_x[0]) + pole * BB->lp_y[0];    /* the Filter knob */
        BB->lp_x[0] = ya;
        ya = BB->lp_y[0];
        if (pingpong) {
            float yb = bbd_lowpass(&BB->rc[1], tape_read(qb, pos, (uint32_t)back, frac));
            BB->lp_y[1] = lp_b * (yb + BB->lp_x[1]) + pole * BB->lp_y[1];
            BB->lp_x[1] = yb;
            yb = BB->lp_y[1];
            BB->hp_y[0] = BBD_LOWCUT * (BB->hp_y[0] + yb - BB->hp_x[0]);
            BB->hp_x[0] = yb;
            BB->hp_y[1] = BBD_LOWCUT * (BB->hp_y[1] + ya - BB->hp_x[1]);
            BB->hp_x[1] = ya;
            float va = bbd_lowpass(&BB->aa[0], bbd_chip_in(x + gain * BB->hp_y[0]));
            float vb = bbd_lowpass(&BB->aa[1], bbd_chip_in(gain * BB->hp_y[1]));
            if (tick || hold >= 1.0f) {
                held0 = va;
                held1 = vb;
            }
            qa[pos] = to16(held0);
            qb[pos] = to16(held1);
            out_l[n] = to16(yb);
            out_r[n] = to16(ya);
        } else {
            BB->hp_y[0] = BBD_LOWCUT * (BB->hp_y[0] + ya - BB->hp_x[0]);
            BB->hp_x[0] = ya;
            float va = bbd_lowpass(&BB->aa[0], bbd_chip_in(x + gain * BB->hp_y[0]));
            if (tick || hold >= 1.0f)
                held0 = va;
            int16_t rec = to16(held0);
            qa[pos] = rec;
            qb[pos] = rec;                  /* so PingPong on carries on from the same line */
            held1 = held0;
            out_l[n] = out_r[n] = to16(ya);
        }
        pos = (pos + 1u) & TAPE_MASK;
    }
    BB->wpos = pos;
    BB->dint = dint;
    BB->dfrac = dfrac;
    BB->phase = phase;
    BB->held[0] = held0;
    BB->held[1] = held1;
}

/* Take the send, run the BBD, send the two return blocks, as the stock update() does. */
__attribute__((unused)) static void bbd_blocks(uint8_t *obj)
{
    static const int16_t silence[BLOCK] = {0};
    uint8_t *in = ((receive_fn)FW_RECEIVE_READONLY)(obj, 0);
    uint8_t *left = ((allocate_fn)FW_ALLOCATE)();
    uint8_t *right = ((allocate_fn)FW_ALLOCATE)();
    if (left && right && !obj[DL_BYPASS]) {
        bbd_process(obj, in ? (const int16_t *)(in + 4) : silence, (int16_t *)(left + 4), (int16_t *)(right + 4));
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
