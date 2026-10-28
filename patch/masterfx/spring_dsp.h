/*
 * spring_dsp.h: a spring reverb on the master reverb object (Polyend Tracker+ firmware
 * 1.2.0), for masterfx.c. Include it once, in one .c file, after freeverb_dsp.h.
 *
 * A model of a two-spring tank, after the structure in Valimaki, Parker and Abel,
 * "Parametric Spring Reverberation Effect" (J. Audio Eng. Soc., 2010), written from the
 * paper's description: each spring is a delay loop (the wave running to the end of the
 * spring and back) around a long chain of first-order allpass filters, which delay the
 * high frequencies more than the low ones (dispersion). Every pass round the loop
 * smears the sound further into a falling chirp, which gives a spring its "drip" and
 * "boing". A lowpass in each loop darkens the repeats.
 *
 * Springs carry little above 4-5 kHz, so the springs run at a quarter of the sample
 * rate (11,025 Hz): the send is summed to mono, delayed by the Predelay, high-passed at
 * 120 Hz (spring tanks are thin), lowpassed at 4 kHz and averaged over four samples;
 * each spring's output is interpolated back up and lowpassed at 4.5 kHz.
 *
 *  - Springs: loops of 408 and 474 samples at 11,025 Hz (37 and 43 ms) for left and
 *    right, each with 32 allpasses; a little of each goes to the other side (20 %), and
 *    each loop's length moves slowly (0.7 and 0.93 Hz, about 1 sample), as a real
 *    spring's does when it sways.
 *  - Resonance: a peak of +0.5 dB around 1 kHz in each loop (the loop gain scaled down by
 *    as much, so it can't run away), so that region rings a little longer than the
 *    rest, and +3 dB there on each spring's output: the "boing".
 *  - Size (+0x54, 0..1)          -> loop gain 0.63..0.98 at the resonance: how long it rings
 *  - Damping (+0x5c = 1 - damping) -> the loop lowpass, 5 kHz down to 1.2 kHz
 *  - Diffusion (+0x58, 0..0.85)  -> the allpass coefficient 0.55..0.85: more drip
 *  - Predelay (+0x6c, 0..5000 samples) -> a delay before the springs
 * The return is mixed as the stock reverb's: send + (wet - send) * amount, with the
 * object's amount (+0x4c) and input gain (+0x50); the bypass flag and the level field
 * (+0x70) as for Freeverb.
 *
 * Memory: in the stock reverb's first buffer (object +0x24): the Predelay line (5,001
 * 16-bit samples), then the two loops and the allpass states as floats. The rest (on-chip,
 * struct spring_state in .mt_ram) is positions and filter states.
 */
#ifndef SPRING_DSP_H
#define SPRING_DSP_H

#include <stdint.h>

#define SP_DECIM            4u              /* the springs run at 44100 / 4 Hz */
#define SP_SPRINGS          2u
#define SP_STAGES           32u             /* allpasses per spring */
#define SP_LINE             512u            /* each loop's ring, samples */
static const uint16_t sp_len[SP_SPRINGS] = {408, 474};
#define SP_SWING            1.1f            /* the loops' movement, samples */
static const float sp_rot[SP_SPRINGS][2] = {    /* per block: cos, sin of 2 pi f 128 / 44100 */
    {0.99991852f, 0.01276549f},             /* 0.7 Hz */
    {0.99985618f, 0.01695951f},             /* 0.93 Hz */
};
#define SP_CROSS            0.2f            /* each spring into the other side */
#define SP_HP               0.98305f        /* send low cut: exp(-2 pi 120 / 44100) */
#define SP_AA               0.43442f        /* before the decimation: 1 - exp(-2 pi 4000 / 44100) */
#define SP_SMOOTH           0.47331f        /* after the interpolation: 1 - exp(-2 pi 4500 / 44100) */
#define SP_LP_OPEN          0.94213f        /* loop lowpass at 11025 Hz: 1 - exp(-2 pi 5000 / 11025) */
#define SP_LP_DARK          0.49535f        /* ... at 1200 Hz */
#define SP_IN_GAIN          0.35f           /* the send into the springs, at the stock input gain 0.1 */
/* the resonance at 11025 Hz: peaking filters at 1 kHz, Q 1 (b0, b1, b2, a1, a2) */
static const float sp_res_loop[5] = {1.01230600f, -1.33418728f, 0.57232775f, -1.33418728f, 0.58463375f};
static const float sp_res_out[5] = {1.07631775f, -1.37239044f, 0.55369045f, -1.37239044f, 0.63000821f};
#define SP_RES_LOOP_GAIN    1.0593f         /* the loop filter's peak (+0.5 dB) */
#define SP_OUT_TRIM         2.0f            /* the springs' return level against the stock reverb's */

/* the floats in the first buffer, after the Predelay line */
struct sp_mem {
    float line[SP_SPRINGS][SP_LINE];
    float ap[SP_SPRINGS][SP_STAGES][2];     /* each allpass's previous input and output */
};
#define SP_MEM_OFFSET       ((2u * PRE_RING + 15u) & ~15u)
_Static_assert(SP_MEM_OFFSET + sizeof(struct sp_mem) <= 2u * BUFFER_SAMPLES, "spring memory exceeds the buffer");

#define SP_MAGIC            0x47525053u     /* "SPRG": laid out for buf_l/buf_r */

struct spring_state {
    uint32_t magic;
    uint32_t buf_l, buf_r;
    uint16_t pre_pos, phase;                /* predelay write position; samples into the 4 */
    uint16_t wpos[SP_SPRINGS];              /* each loop's write position */
    float hp_x, hp_y, aa, acc;              /* the send filters and the decimation sum */
    float lp[SP_SPRINGS];                   /* each loop's lowpass */
    float prev[SP_SPRINGS], cur[SP_SPRINGS];    /* the springs' last two outputs */
    float sm[SP_SPRINGS];                   /* the output smoothing */
    float osc[SP_SPRINGS][2];               /* cos, sin of each loop's movement */
    float res[SP_SPRINGS][4];               /* the resonance filters' states: loop, output */
};
static struct spring_state spring_state_ram __attribute__((section(".mt_ram")));
#define SP (&spring_state_ram)

/* Lay out the springs in the (cleared) first buffer. */
__attribute__((unused)) static void spring_init(uint32_t buf_l, uint32_t buf_r)
{
    SP->pre_pos = 0;
    SP->phase = 0;
    SP->hp_x = SP->hp_y = SP->aa = SP->acc = 0.0f;
    for (uint32_t s = 0; s < SP_SPRINGS; s++) {
        SP->wpos[s] = 0;
        SP->lp[s] = SP->prev[s] = SP->cur[s] = SP->sm[s] = 0.0f;
        SP->osc[s][0] = 1.0f;
        SP->osc[s][1] = 0.0f;
        for (uint32_t k = 0; k < 4; k++)
            SP->res[s][k] = 0.0f;
    }
    SP->buf_l = buf_l;
    SP->buf_r = buf_r;
    SP->magic = SP_MAGIC;
}

/* a peaking filter (transposed direct form II): st holds its two states */
static inline float sp_peak(const float *c, float *st, float x)
{
    float y = c[0] * x + st[0];
    st[0] = c[1] * x - c[3] * y + st[1];
    st[1] = c[2] * x - c[4] * y;
    return y;
}

/* One step of spring s at 11,025 Hz: returns its output. */
static inline float spring_step(struct sp_mem *mem, uint32_t s, float x, float fb, float a, float lp_k, float swing)
{
    float *line = mem->line[s];
    uint32_t w = SP->wpos[s];
    float back = (float)sp_len[s] + swing;
    uint32_t whole = (uint32_t)back;
    float frac = back - (float)whole;
    uint32_t i0 = w >= whole ? w - whole : w + SP_LINE - whole;
    uint32_t i1 = i0 ? i0 - 1u : SP_LINE - 1u;
    float d = line[i0] * (1.0f - frac) + line[i1] * frac;

    /* dispersion: y = a v + v1 - a y1, per stage */
    float v = x + fb * d;
    float (*ap)[2] = mem->ap[s];
    for (uint32_t k = 0; k < SP_STAGES; k++) {
        float y = a * (v - ap[k][1]) + ap[k][0];
        ap[k][0] = v;
        ap[k][1] = y;
        v = y;
    }
    float lp = SP->lp[s] + lp_k * (v - SP->lp[s]);
    SP->lp[s] = lp;
    line[w] = sp_peak(sp_res_loop, &SP->res[s][0], lp);
    SP->wpos[s] = (uint16_t)(w + 1u == SP_LINE ? 0u : w + 1u);
    return sp_peak(sp_res_out, &SP->res[s][2], v);
}

/* Process one block of the stereo send in place; returns the average output level. */
static float spring_process(uint8_t *obj, int16_t *left, int16_t *right)
{
    int16_t *pre = (int16_t *)SP->buf_l;
    struct sp_mem *mem = (struct sp_mem *)(SP->buf_l + SP_MEM_OFFSET);
    float size = fclamp(*(volatile float *)(obj + RV_SIZE), 0.0f, 1.0f);
    float damping = fclamp(1.0f - *(volatile float *)(obj + RV_LP), 0.0f, 1.0f);
    float diffusion = fclamp(*(volatile float *)(obj + RV_DIFFUSION), 0.0f, 1.0f);
    float amount = fclamp(*(volatile float *)(obj + RV_AMOUNT), 0.0f, 1.0f);
    float gain = fclamp(*(volatile float *)(obj + RV_INPUT_GAIN), 0.0f, 1.0f);
    int32_t predelay = *(volatile int32_t *)(obj + RV_PREDELAY);
    if (predelay < 0)
        predelay = 0;
    if (predelay > (int32_t)PREDELAY_MAX)
        predelay = PREDELAY_MAX;

    float fb = (0.63f + 0.35f * size) * (1.0f / SP_RES_LOOP_GAIN);
    float lp_k = SP_LP_OPEN + (SP_LP_DARK - SP_LP_OPEN) * damping;
    float a = 0.55f + 0.3f * fclamp(diffusion / 0.85f, 0.0f, 1.0f);
    float in_gain = SP_IN_GAIN * (gain / 0.1f);

    /* each loop's movement, once per block */
    float swing[SP_SPRINGS];
    for (uint32_t s = 0; s < SP_SPRINGS; s++) {
        float c = SP->osc[s][0], sn = SP->osc[s][1];
        float c2 = c * sp_rot[s][0] - sn * sp_rot[s][1], s2 = sn * sp_rot[s][0] + c * sp_rot[s][1];
        float g = 1.5f - 0.5f * (c2 * c2 + s2 * s2);
        SP->osc[s][0] = c2 * g;
        SP->osc[s][1] = s2 * g;
        swing[s] = SP_SWING * SP->osc[s][1];
    }

    uint32_t wr = SP->pre_pos, phase = SP->phase;
    float hp_x = SP->hp_x, hp_y = SP->hp_y, aa = SP->aa, acc = SP->acc;
    /* the springs' last two outputs and the smoothing, in locals across the block (the
     * compiler otherwise reloads them around spring_step every sample) */
    float p0 = SP->prev[0], p1 = SP->prev[1], c0 = SP->cur[0], c1 = SP->cur[1];
    float sm0 = SP->sm[0], sm1 = SP->sm[1];
    float level = 0.0f;
    for (uint32_t n = 0; n < BLOCK; n++) {
        /* the predelay in integers ((l + r) / 2 cuts the half toward zero, as the old
         * float write's sat16 did), then the send filters */
        pre[wr] = (int16_t)(((int32_t)left[n] + (int32_t)right[n]) / 2);
        uint32_t rd = wr + PRE_RING - (uint32_t)predelay;
        if (rd >= PRE_RING)
            rd -= PRE_RING;
        float x = (float)pre[rd] * in_gain;
        if (++wr == PRE_RING)
            wr = 0;
        hp_y = SP_HP * (hp_y + x - hp_x);
        hp_x = x;
        aa += SP_AA * (hp_y - aa);
        acc += aa;

        /* every fourth sample, a step of the springs */
        if (++phase == SP_DECIM) {
            phase = 0;
            float xd = acc * (1.0f / SP_DECIM);
            acc = 0.0f;
            p0 = c0;
            c0 = spring_step(mem, 0, xd, fb, a, lp_k, swing[0]);
            p1 = c1;
            c1 = spring_step(mem, 1, xd, fb, a, lp_k, swing[1]);
        }
        /* back up to 44.1 kHz: between the last two outputs, then smoothed */
        float t = (float)phase * (1.0f / SP_DECIM);
        float o0 = p0 + (c0 - p0) * t;
        float o1 = p1 + (c1 - p1) * t;
        sm0 += SP_SMOOTH * (o0 + SP_CROSS * o1 - sm0);
        sm1 += SP_SMOOTH * (o1 + SP_CROSS * o0 - sm1);

        float wl = sm0 * SP_OUT_TRIM, wrt = sm1 * SP_OUT_TRIM;
        float dl = (float)left[n], dr = (float)right[n];
        left[n] = sat16(dl + (wl - dl) * amount);
        right[n] = sat16(dr + (wrt - dr) * amount);
        level += (wl < 0 ? -wl : wl) + (wrt < 0 ? -wrt : wrt);
    }
    SP->pre_pos = (uint16_t)wr;
    SP->phase = (uint16_t)phase;
    SP->hp_x = hp_x;
    SP->hp_y = hp_y;
    SP->aa = aa;
    SP->acc = acc;
    SP->prev[0] = p0;
    SP->prev[1] = p1;
    SP->cur[0] = c0;
    SP->cur[1] = c1;
    SP->sm[0] = sm0;
    SP->sm[1] = sm1;
    return level / BLOCK;
}

__attribute__((unused)) static void spring_blocks(uint8_t *obj)
{
    uint8_t *left = ((receive_fn)FW_RECEIVE_WRITABLE)(obj, 0);
    if (!left)
        return;
    uint8_t *right = ((receive_fn)FW_RECEIVE_WRITABLE)(obj, 1);
    if (!right) {
        ((release_fn)FW_RELEASE)(left);
        return;
    }
    *(volatile float *)(obj + RV_LEVEL) = spring_process(obj, (int16_t *)(left + 4), (int16_t *)(right + 4));
    ((transmit_fn)FW_TRANSMIT)(obj, left, 0);
    ((transmit_fn)FW_TRANSMIT)(obj, right, 1);
    ((release_fn)FW_RELEASE)(left);
    ((release_fn)FW_RELEASE)(right);
}

#endif
