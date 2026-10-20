/*
 * gated_dsp.h: a "gated" reverb on the master delay object (Polyend Tracker+ firmware
 * 1.2.0), for masterfx.c. Include it once, in one .c file, after tape_dsp.h (it uses its
 * field names, block helpers and helpers).
 *
 * Not a reverb behind a noise gate, but what the classic digital units (the AMS RMX16's
 * "non-linear" programs) did: a dense burst of reflections that lasts exactly as long as
 * set and then stops. It runs on the delay object, so the Master Delay's time controls
 * set its length, in milliseconds or in step with the tempo:
 *
 *  - The send is smeared by three short allpass diffusers (37, 53 and 71 samples) and
 *    written into a delay line (the stock delay's first buffer); 20 taps per side, spread
 *    unevenly over the gate length (different on each side), add up the burst (in
 *    integers, with Q14 gains, five taps at a time); each side is smeared again by three
 *    short allpasses (89, 131 and 173 samples left, 97, 149 and 197 right) into a dense
 *    wash. The diffusers are kept short and light so the burst still stops sharply:
 *    they ring on for well under 50 ms.
 *  - Each tap's level follows the gate's shape over the gate length: a 50 ms attack (or a
 *    quarter of the length, if shorter), a hold, and a release at the end, then nothing.
 *
 * The Master Delay knobs:
 *  - Time, or Sync and Rate (+0x28) -> the gate length (10 ms to 2 s)
 *  - Feedback (+0x2c, 0..252)  -> the release: from a hard cut at 0 to a fade over the
 *                                 whole length at the top (there is no feedback)
 *  - Filter (the pole at +0x38) -> the tone, the stock delay's lowpass on the output
 *  - PingPong (+0x2e)          -> reverse: the burst swells up over the length and then
 *                                 cuts off, instead of starting at full level
 *  - the bypass flag (+0x2d) as for the stock delay
 *
 * The state (struct gated_state) is in .mt_ram, placed by the patcher.
 */
#ifndef GATED_DSP_H
#define GATED_DSP_H

#include <stdint.h>

#define GT_TAPS             20u
#define GT_GROUP            5u              /* taps summed per pass */
_Static_assert(GT_TAPS % GT_GROUP == 0 && GT_GROUP == 5u, "the taps go in groups of five");
static const float gt_pos[2][GT_TAPS] = {   /* each tap's place in the gate, 0..1 */
    {0.0409f, 0.0740f, 0.1287f, 0.1665f, 0.2276f, 0.2884f, 0.3231f, 0.3902f, 0.4404f, 0.4836f,
     0.5398f, 0.5591f, 0.6100f, 0.6804f, 0.7286f, 0.7670f, 0.8111f, 0.8892f, 0.9098f, 0.9832f},
    {0.0394f, 0.0627f, 0.1383f, 0.1649f, 0.2186f, 0.2823f, 0.3225f, 0.3759f, 0.4176f, 0.4861f,
     0.5227f, 0.5817f, 0.6104f, 0.6749f, 0.7398f, 0.7720f, 0.8144f, 0.8847f, 0.9404f, 0.9665f},
};
#define GT_MIN              441u            /* gate length, samples: 10 ms .. */
#define GT_MAX              88200u          /* .. 2 s */
#define GT_ATTACK           2205.0f         /* 50 ms */
#define GT_TAP_GAIN         0.2236f         /* 1 / sqrt(GT_TAPS): the burst at about the send's level */
#define GT_Q                16384.0f        /* the taps' gains in Q14 (20 x 32768 x 0.22 x 16384 fits 32 bits) */
#define GT_DIFFUSE          0.6f            /* the send's diffusers */
#define GT_SMEAR            0.5f            /* each side's diffusers */

/* the allpasses: one ring (the stock delay's second buffer) with a moving write
 * position, as in plate_dsp.h; each line takes len + 1 places */
#define GL_PRE1             37u
#define GL_PRE2             53u
#define GL_PRE3             71u
#define GL_L1               89u
#define GL_L2               131u
#define GL_L3               173u
#define GL_R1               97u
#define GL_R2               149u
#define GL_R3               197u
#define GB_PRE1             0u
#define GB_PRE2             (GB_PRE1 + GL_PRE1 + 1u)
#define GB_PRE3             (GB_PRE2 + GL_PRE2 + 1u)
#define GB_L1               (GB_PRE3 + GL_PRE3 + 1u)
#define GB_L2               (GB_L1 + GL_L1 + 1u)
#define GB_L3               (GB_L2 + GL_L2 + 1u)
#define GB_R1               (GB_L3 + GL_L3 + 1u)
#define GB_R2               (GB_R1 + GL_R1 + 1u)
#define GB_R3               (GB_R2 + GL_R2 + 1u)

#define GT_MAGIC            0x45544147u     /* "GATE": laid out for buf[] */

struct gated_state {
    uint32_t magic;
    uint32_t buf[2];                        /* the tap line, and the allpasses' ring */
    uint32_t wpos;                          /* the tap line's write position */
    uint32_t wp;                            /* the allpasses' ring position */
    float lp_x[2], lp_y[2];                 /* the Filter knob's lowpass */
};
static struct gated_state gated_state_ram __attribute__((section(".mt_ram")));
#define GT (&gated_state_ram)

#define GT_RD(off)          ((float)ap[(wp + (off)) & TAPE_MASK])
#define GT_WR(off, v)       (ap[(wp + (off)) & TAPE_MASK] = sat16(v))

/* Lay out the lines in buffers 0 and 1 (cleared beforehand). */
__attribute__((unused)) static void gated_init(uint32_t buf0, uint32_t buf1)
{
    GT->buf[0] = buf0;
    GT->buf[1] = buf1;
    GT->wpos = 0;
    GT->wp = 0;
    GT->lp_x[0] = GT->lp_x[1] = GT->lp_y[0] = GT->lp_y[1] = 0.0f;
    GT->magic = GT_MAGIC;
}

/* the gate's level at place t (0..1) of the length */
static inline float gt_shape(float t, float attack, float release, int reverse)
{
    float g = reverse ? t : (t < attack ? t / attack : 1.0f);
    if (release > 0.0f && t > 1.0f - release)
        g *= (1.0f - t) / release;
    return g;
}

/* Run the burst over one block: mono send in, left and right out. */
static void gated_process(const uint8_t *obj, const int16_t *in, int16_t *out_l, int16_t *out_r)
{
    int16_t *line = (int16_t *)GT->buf[0], *ap = (int16_t *)GT->buf[1];
    uint32_t len = *(volatile uint32_t *)(obj + DL_TIME);
    if (len < GT_MIN)
        len = GT_MIN;
    if (len > GT_MAX)
        len = GT_MAX;
    int reverse = *(volatile uint8_t *)(obj + DL_PINGPONG) != 0;
    float fb = (float)*(volatile uint8_t *)(obj + DL_FEEDBACK) * (1.0f / 252.0f);
    if (fb > 1.0f)
        fb = 1.0f;
    float pole = -(float)*(volatile int32_t *)(obj + DL_FILTER_POLE) * (1.0f / 1023.0f);
    if (!(pole >= POLE_MIN))
        pole = POLE_MIN;
    if (pole > POLE_MAX)
        pole = POLE_MAX;
    float lp_b = 0.5f * (1.0f - pole);

    /* the taps for this length: where they are and how loud */
    float attack = GT_ATTACK / (float)len;
    if (attack > 0.25f)
        attack = 0.25f;
    float release = fb * (1.0f - attack);
    uint32_t back[2][GT_TAPS];
    int32_t gain[2][GT_TAPS];
    for (uint32_t s = 0; s < 2; s++)
        for (uint32_t k = 0; k < GT_TAPS; k++) {
            uint32_t d = (uint32_t)(gt_pos[s][k] * (float)len);
            back[s][k] = d ? d : 1u;
            gain[s][k] = (int32_t)(GT_Q * GT_TAP_GAIN * gt_shape(gt_pos[s][k], attack, release, reverse));
        }

    uint32_t pos0 = GT->wpos, wp0 = GT->wp, pos = pos0, wp = wp0;
    float d, y;
    /* 1. the send, diffused, onto the tap line */
    for (uint32_t n = 0; n < BLOCK; n++) {
        float v = (float)in[n];
        d = GT_RD(GB_PRE1 + GL_PRE1); y = d - GT_DIFFUSE * v; GT_WR(GB_PRE1, v + GT_DIFFUSE * y); v = y;
        d = GT_RD(GB_PRE2 + GL_PRE2); y = d - GT_DIFFUSE * v; GT_WR(GB_PRE2, v + GT_DIFFUSE * y); v = y;
        d = GT_RD(GB_PRE3 + GL_PRE3); y = d - GT_DIFFUSE * v; GT_WR(GB_PRE3, v + GT_DIFFUSE * y); v = y;
        line[pos] = sat16(v);
        pos = (pos + 1u) & TAPE_MASK;
        wp = (wp - 1u) & TAPE_MASK;
    }
    /* 2. the burst: five taps at a time over the block (each tap's samples are in a
     * row; the pass is split where one of the five wraps); the samples this block wrote
     * are already there */
    int32_t acc[2][BLOCK];
    for (uint32_t s = 0; s < 2; s++) {
        int32_t *a = acc[s];
        for (uint32_t grp = 0; grp < GT_TAPS; grp += GT_GROUP) {
            uint32_t start[GT_GROUP];
            int32_t g[GT_GROUP];
            for (uint32_t j = 0; j < GT_GROUP; j++) {
                start[j] = (pos0 - back[s][grp + j]) & TAPE_MASK;
                g[j] = gain[s][grp + j];
            }
            for (uint32_t n = 0; n < BLOCK;) {
                uint32_t run = BLOCK - n;
                for (uint32_t j = 0; j < GT_GROUP; j++)
                    if (TAPE_LEN - start[j] < run)
                        run = TAPE_LEN - start[j];
                const int16_t *q0 = line + start[0], *q1 = line + start[1], *q2 = line + start[2];
                const int16_t *q3 = line + start[3], *q4 = line + start[4];
                int32_t *o = a + n;
                if (grp == 0)
                    for (uint32_t i = 0; i < run; i++)
                        o[i] = g[0] * q0[i] + g[1] * q1[i] + g[2] * q2[i] + g[3] * q3[i] + g[4] * q4[i];
                else
                    for (uint32_t i = 0; i < run; i++)
                        o[i] += g[0] * q0[i] + g[1] * q1[i] + g[2] * q2[i] + g[3] * q3[i] + g[4] * q4[i];
                for (uint32_t j = 0; j < GT_GROUP; j++)
                    start[j] = (start[j] + run) & TAPE_MASK;
                n += run;
            }
        }
    }
    /* 3. each side smeared into a wash, then the tone */
    wp = wp0;
    float lpx0 = GT->lp_x[0], lpy0 = GT->lp_y[0], lpx1 = GT->lp_x[1], lpy1 = GT->lp_y[1];
    for (uint32_t n = 0; n < BLOCK; n++) {
        float l = (float)acc[0][n] * (1.0f / GT_Q), r = (float)acc[1][n] * (1.0f / GT_Q);
        d = GT_RD(GB_L1 + GL_L1); y = d - GT_SMEAR * l; GT_WR(GB_L1, l + GT_SMEAR * y); l = y;
        d = GT_RD(GB_L2 + GL_L2); y = d - GT_SMEAR * l; GT_WR(GB_L2, l + GT_SMEAR * y); l = y;
        d = GT_RD(GB_L3 + GL_L3); y = d - GT_SMEAR * l; GT_WR(GB_L3, l + GT_SMEAR * y); l = y;
        d = GT_RD(GB_R1 + GL_R1); y = d - GT_SMEAR * r; GT_WR(GB_R1, r + GT_SMEAR * y); r = y;
        d = GT_RD(GB_R2 + GL_R2); y = d - GT_SMEAR * r; GT_WR(GB_R2, r + GT_SMEAR * y); r = y;
        d = GT_RD(GB_R3 + GL_R3); y = d - GT_SMEAR * r; GT_WR(GB_R3, r + GT_SMEAR * y); r = y;
        lpy0 = lp_b * (l + lpx0) + pole * lpy0;
        lpx0 = l;
        lpy1 = lp_b * (r + lpx1) + pole * lpy1;
        lpx1 = r;
        out_l[n] = sat16(lpy0);
        out_r[n] = sat16(lpy1);
        wp = (wp - 1u) & TAPE_MASK;
    }
    GT->wpos = pos;
    GT->wp = wp;
    GT->lp_x[0] = lpx0;
    GT->lp_y[0] = lpy0;
    GT->lp_x[1] = lpx1;
    GT->lp_y[1] = lpy1;
}

/* Take the send, run the burst, send the two return blocks, as the stock update() does. */
__attribute__((unused)) static void gated_blocks(uint8_t *obj)
{
    static const int16_t silence[BLOCK] = {0};
    uint8_t *in = ((receive_fn)FW_RECEIVE_READONLY)(obj, 0);
    uint8_t *left = ((allocate_fn)FW_ALLOCATE)();
    uint8_t *right = ((allocate_fn)FW_ALLOCATE)();
    if (left && right && !obj[DL_BYPASS]) {
        gated_process(obj, in ? (const int16_t *)(in + 4) : silence, (int16_t *)(left + 4), (int16_t *)(right + 4));
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
