/*
 * shimmer_dsp.h: a shimmer reverb on the master reverb object (Polyend Tracker+ firmware
 * 1.2.0), for masterfx.c. Include it once, in one .c file, after freeverb_dsp.h.
 *
 * A shimmer reverb is a reverb with a pitch shifter in its feedback: the tail is shifted
 * up an octave and fed back into the reverb, so every pass adds a layer an octave
 * higher, and the tail rises into a bright, organ-like wash.
 *
 * The reverb is the same plate as plate_dsp.h (Dattorro, "Effect Design, Part 1", 1997:
 * input diffusers, a two-half tank, seven output taps per side), here at the paper's own
 * delay lengths (as if at 29.761 kHz), which leaves room in the stock reverb's two
 * buffers for the pitch shifter's line next to the Predelay. Every pass:
 *  - the plate's output (both sides, summed) is written into the shifter's line
 *    (2,048 samples, 46 ms); two read heads move through it at twice the speed it is
 *    written, half a line apart, each faded in and out with a triangle window as it
 *    wraps, so together they play it back an octave up without gaps;
 *  - the shifted signal is band-limited (200 Hz to 6 kHz, so the octaves don't pile up
 *    into a whistle or a rumble), limited (so the loop can't run away, whatever the
 *    knobs), scaled by the shimmer amount and added to the plate's input.
 *
 * The Master Reverb knobs:
 *  - Size (+0x54, 0..1)          -> the plate's decay 0.35..0.97: how long it rings
 *  - Damping (+0x5c = 1 - damping) -> the plate's damping: the tail's tone
 *  - Predelay (+0x6c, 0..5000 samples) -> a delay before the reverb, as for the others
 *  - Diffusion (+0x58, 0..0.85)  -> the shimmer amount: none at 0 (a plain plate) to full
 *                                   at the top; the plate's own diffusion is fixed at
 *                                   the paper's values
 * The return is mixed as the stock reverb's: send + (wet - send) * amount, with the
 * object's amount (+0x4c) and input gain (+0x50); the bypass flag and the level field
 * (+0x70) as for the others.
 *
 * The state (struct shimmer_state) is in .mt_ram, placed by the patcher.
 */
#ifndef SHIMMER_DSP_H
#define SHIMMER_DSP_H

#include <stdint.h>

/* ---- the lines (the paper's lengths) ------------------------------------------------ */
#define SH_EXCURSION        16u             /* the modulated allpasses' swing, samples */
#define SH_AP_A_LEN         672u
#define SH_AP_B_LEN         908u
#define SH_WINDOW           2048u           /* the pitch shifter's line */

#define S_IN1               142u
#define S_IN2               107u
#define S_IN3               379u
#define S_IN4               277u
#define S_AP_A              (SH_AP_A_LEN + SH_EXCURSION + 2u)
#define S_D1_A              4453u
#define S_AP2_A             1800u
#define S_D2_A              3720u
#define S_AP_B              (SH_AP_B_LEN + SH_EXCURSION + 2u)
#define S_D1_B              4217u
#define S_AP2_B             2656u
#define S_D2_B              3163u
/* ring 0 (the first buffer): the input diffusers and the left half */
#define SB_IN1              0u
#define SB_IN2              (SB_IN1 + S_IN1 + 1u)
#define SB_IN3              (SB_IN2 + S_IN2 + 1u)
#define SB_IN4              (SB_IN3 + S_IN3 + 1u)
#define SB_AP_A             (SB_IN4 + S_IN4 + 1u)
#define SB_D1_A             (SB_AP_A + S_AP_A + 1u)
#define SB_AP2_A            (SB_D1_A + S_D1_A + 1u)
#define SB_D2_A             (SB_AP2_A + S_AP2_A + 1u)
#define SB_PRE0             (SB_D2_A + S_D2_A + 1u)
#define S_PRE0              (BUFFER_SAMPLES - SB_PRE0 - 1u)
/* ring 1 (the second buffer): the right half and the pitch shifter */
#define SB_AP_B             0u
#define SB_D1_B             (SB_AP_B + S_AP_B + 1u)
#define SB_AP2_B            (SB_D1_B + S_D1_B + 1u)
#define SB_D2_B             (SB_AP2_B + S_AP2_B + 1u)
#define SB_SHIFT            (SB_D2_B + S_D2_B + 1u)
#define SB_PRE1             (SB_SHIFT + SH_WINDOW + 1u)
#define S_PRE1              (BUFFER_SAMPLES - SB_PRE1 - 1u)
#define SH_MASK             (BUFFER_SAMPLES - 1u)
#if SB_PRE0 + 1u > BUFFER_SAMPLES || SB_PRE1 + 1u > BUFFER_SAMPLES || S_PRE0 + S_PRE1 < PREDELAY_MAX
#error "the shimmer's lines and the predelay don't fit the two buffers"
#endif

#define SH_BANDWIDTH        0.9995f
#define SH_TAP_GAIN         0.6f
#define SH_IN_GAIN          0.5f            /* the send into the plate, at the stock input gain 0.1 */
#define SH_OUT_TRIM         0.5f            /* the return level against the stock reverb's */
#define SH_ID1              0.75f           /* the plate's diffusion: the paper's values */
#define SH_ID2              0.625f
#define SH_DD1              0.70f
#define SH_AMOUNT_MAX       0.6f            /* the shifted feed at the top of the Diffusion knob */
#define SH_HP               0.97191f        /* the feed's low cut: exp(-2 pi 200 / 44100) */
#define SH_LP               0.57466f        /* ... and high cut: 1 - exp(-2 pi 6000 / 44100) */
#define SH_LIMIT            4000.0f         /* the feed's ceiling, sample units */
#define SH_RELEASE          0.99989f        /* the limiter's release: about 0.2 s */
#define SH_ROT_C            0.99983371f     /* the modulation: 1 Hz, per block */
#define SH_ROT_S            0.01823676f

#define SH_MAGIC            0x4D4D4853u     /* "SHMM": laid out for buf_l/buf_r */

struct shimmer_state {
    uint32_t magic;
    uint32_t buf_l, buf_r;
    uint32_t wp;                            /* the rings' write position */
    uint16_t head[2];                       /* the shifter's read heads: samples back, 1..SH_WINDOW */
    float bw;
    float damp[2];
    float osc[2];
    float hp_x, hp_y, lp, env, feed;        /* the shifted feed: filters, limiter, last value */
};
static struct shimmer_state shimmer_state_ram __attribute__((section(".mt_ram")));
#define SH (&shimmer_state_ram)

#define SH_RD0(off)         ((float)q0[(wp + (off)) & SH_MASK])
#define SH_RD1(off)         ((float)q1[(wp + (off)) & SH_MASK])
#define SH_WR0(off, v)      (q0[(wp + (off)) & SH_MASK] = sat16(v))
#define SH_WR1(off, v)      (q1[(wp + (off)) & SH_MASK] = sat16(v))

/* Lay out the rings in the two (cleared) buffers. */
__attribute__((unused)) static void shimmer_init(uint32_t buf_l, uint32_t buf_r)
{
    SH->wp = 0;
    SH->head[0] = SH_WINDOW;
    SH->head[1] = SH_WINDOW / 2u;
    SH->bw = 0.0f;
    SH->damp[0] = SH->damp[1] = 0.0f;
    SH->osc[0] = 1.0f;
    SH->osc[1] = 0.0f;
    SH->hp_x = SH->hp_y = SH->lp = SH->env = SH->feed = 0.0f;
    SH->buf_l = buf_l;
    SH->buf_r = buf_r;
    SH->magic = SH_MAGIC;
}

static inline float sh_window(uint32_t head)    /* a triangle: 0 at the ends, 1 in the middle */
{
    float t = (float)head * (2.0f / SH_WINDOW) - 1.0f;
    return 1.0f - (t < 0 ? -t : t);
}

/* Process one block of the stereo send in place; returns the average output level. */
static float shimmer_process(uint8_t *obj, int16_t *left, int16_t *right)
{
    int16_t *q0 = (int16_t *)SH->buf_l, *q1 = (int16_t *)SH->buf_r;
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

    float decay = 0.35f + 0.62f * size;
    float dd2 = fclamp(decay + 0.15f, 0.25f, 0.5f);
    float damp = 0.85f * damping, keep = 1.0f - damp;
    float in_gain = SH_IN_GAIN * (gain / 0.1f);
    float shimmer = SH_AMOUNT_MAX * fclamp(diffusion / 0.85f, 0.0f, 1.0f);
    const float tg = SH_TAP_GAIN * SH_OUT_TRIM;

    float c = SH->osc[0], s = SH->osc[1];
    float c2 = c * SH_ROT_C - s * SH_ROT_S, s2 = s * SH_ROT_C + c * SH_ROT_S;
    float g = 1.5f - 0.5f * (c2 * c2 + s2 * s2);
    c2 *= g;
    s2 *= g;
    SH->osc[0] = c2;
    SH->osc[1] = s2;
    float sa = s * (float)SH_EXCURSION, sa_step = (s2 - s) * (float)SH_EXCURSION / BLOCK;
    float ca = c * (float)SH_EXCURSION, ca_step = (c2 - c) * (float)SH_EXCURSION / BLOCK;

    uint32_t pre0 = (uint32_t)predelay < S_PRE0 ? (uint32_t)predelay : S_PRE0;
    uint32_t pre1 = (uint32_t)predelay - pre0;

    float bw = SH->bw, lp_a = SH->damp[0], lp_b = SH->damp[1];
    float hp_x = SH->hp_x, hp_y = SH->hp_y, flp = SH->lp, env = SH->env, feed = SH->feed;
    uint32_t h0 = SH->head[0], h1 = SH->head[1];
    uint32_t wp = SH->wp;
    float level = 0.0f;
    for (uint32_t n = 0; n < BLOCK; n++) {
        float x = ((float)left[n] + (float)right[n]) * 0.5f;
        SH_WR0(SB_PRE0, x);
        if (pre0)
            x = SH_RD0(SB_PRE0 + pre0);
        SH_WR1(SB_PRE1, x);
        if (pre1)
            x = SH_RD1(SB_PRE1 + pre1);
        x = x * in_gain + feed;             /* the send, and the shifted tail */

        float out_l = SH_RD1(SB_D1_B + 266u) + SH_RD1(SB_D1_B + 2974u) - SH_RD1(SB_AP2_B + 1913u)
                      + SH_RD1(SB_D2_B + 1996u) - SH_RD0(SB_D1_A + 1990u) - SH_RD0(SB_AP2_A + 187u)
                      - SH_RD0(SB_D2_A + 1066u);
        float out_r = SH_RD0(SB_D1_A + 353u) + SH_RD0(SB_D1_A + 3627u) - SH_RD0(SB_AP2_A + 1228u)
                      + SH_RD0(SB_D2_A + 2673u) - SH_RD1(SB_D1_B + 2111u) - SH_RD1(SB_AP2_B + 335u)
                      - SH_RD1(SB_D2_B + 121u);
        float end_a = SH_RD0(SB_D2_A + S_D2_A), end_b = SH_RD1(SB_D2_B + S_D2_B);

        bw = SH_BANDWIDTH * x + (1.0f - SH_BANDWIDTH) * bw;
        float v = bw, d, y;
        d = SH_RD0(SB_IN1 + S_IN1); y = d - SH_ID1 * v; SH_WR0(SB_IN1, v + SH_ID1 * y); v = y;
        d = SH_RD0(SB_IN2 + S_IN2); y = d - SH_ID1 * v; SH_WR0(SB_IN2, v + SH_ID1 * y); v = y;
        d = SH_RD0(SB_IN3 + S_IN3); y = d - SH_ID2 * v; SH_WR0(SB_IN3, v + SH_ID2 * y); v = y;
        d = SH_RD0(SB_IN4 + S_IN4); y = d - SH_ID2 * v; SH_WR0(SB_IN4, v + SH_ID2 * y); v = y;

        float in_a = v + decay * end_b;
        float dl = (float)SH_AP_A_LEN + sa;
        uint32_t whole = (uint32_t)dl;
        float frac = dl - (float)whole;
        d = SH_RD0(SB_AP_A + whole) * (1.0f - frac) + SH_RD0(SB_AP_A + whole + 1u) * frac;
        y = d + SH_DD1 * in_a;
        SH_WR0(SB_AP_A, in_a - SH_DD1 * y);
        float a = SH_RD0(SB_D1_A + S_D1_A);
        SH_WR0(SB_D1_A, y);
        lp_a = keep * a + damp * lp_a;
        float u = lp_a * decay;
        d = SH_RD0(SB_AP2_A + S_AP2_A); y = d - dd2 * u; SH_WR0(SB_AP2_A, u + dd2 * y);
        SH_WR0(SB_D2_A, y);

        float in_b = v + decay * end_a;
        dl = (float)SH_AP_B_LEN + ca;
        whole = (uint32_t)dl;
        frac = dl - (float)whole;
        d = SH_RD1(SB_AP_B + whole) * (1.0f - frac) + SH_RD1(SB_AP_B + whole + 1u) * frac;
        y = d + SH_DD1 * in_b;
        SH_WR1(SB_AP_B, in_b - SH_DD1 * y);
        float b = SH_RD1(SB_D1_B + S_D1_B);
        SH_WR1(SB_D1_B, y);
        lp_b = keep * b + damp * lp_b;
        u = lp_b * decay;
        d = SH_RD1(SB_AP2_B + S_AP2_B); y = d - dd2 * u; SH_WR1(SB_AP2_B, u + dd2 * y);
        SH_WR1(SB_D2_B, y);

        /* the octave up: the plate's output into the shifter's line, two heads reading it
         * at twice the speed (each one sample closer every sample), cross-faded */
        float tank = (out_l + out_r) * (0.5f * SH_TAP_GAIN);
        SH_WR1(SB_SHIFT, tank);
        float shifted = SH_RD1(SB_SHIFT + h0) * sh_window(h0) + SH_RD1(SB_SHIFT + h1) * sh_window(h1);
        h0 = h0 > 1u ? h0 - 1u : SH_WINDOW;
        h1 = h1 > 1u ? h1 - 1u : SH_WINDOW;
        hp_y = SH_HP * (hp_y + shifted - hp_x);
        hp_x = shifted;
        flp += SH_LP * (hp_y - flp);
        float mag = flp < 0 ? -flp : flp;
        env = mag > env ? mag : env * SH_RELEASE;
        feed = flp * shimmer * (env > SH_LIMIT ? SH_LIMIT / env : 1.0f);

        sa += sa_step;
        ca += ca_step;
        wp = (wp - 1u) & SH_MASK;

        float wl = out_l * tg, wrt = out_r * tg;
        float dry_l = (float)left[n], dry_r = (float)right[n];
        left[n] = sat16(dry_l + (wl - dry_l) * amount);
        right[n] = sat16(dry_r + (wrt - dry_r) * amount);
        level += (wl < 0 ? -wl : wl) + (wrt < 0 ? -wrt : wrt);
    }
    SH->wp = wp;
    SH->head[0] = (uint16_t)h0;
    SH->head[1] = (uint16_t)h1;
    SH->bw = bw;
    SH->damp[0] = lp_a;
    SH->damp[1] = lp_b;
    SH->hp_x = hp_x;
    SH->hp_y = hp_y;
    SH->lp = flp;
    SH->env = env;
    SH->feed = feed;
    return level / BLOCK;
}

__attribute__((unused)) static void shimmer_blocks(uint8_t *obj)
{
    uint8_t *left = ((receive_fn)FW_RECEIVE_WRITABLE)(obj, 0);
    if (!left)
        return;
    uint8_t *right = ((receive_fn)FW_RECEIVE_WRITABLE)(obj, 1);
    if (!right) {
        ((release_fn)FW_RELEASE)(left);
        return;
    }
    *(volatile float *)(obj + RV_LEVEL) = shimmer_process(obj, (int16_t *)(left + 4), (int16_t *)(right + 4));
    ((transmit_fn)FW_TRANSMIT)(obj, left, 0);
    ((transmit_fn)FW_TRANSMIT)(obj, right, 1);
    ((release_fn)FW_RELEASE)(left);
    ((release_fn)FW_RELEASE)(right);
}

#endif
