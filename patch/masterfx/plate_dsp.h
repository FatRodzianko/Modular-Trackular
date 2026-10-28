/*
 * plate_dsp.h: a plate reverb on the master reverb object (Polyend Tracker+ firmware
 * 1.2.0), for masterfx.c. Include it once, in one .c file, after freeverb_dsp.h and
 * shimmer_dsp.h (the output taps run through shimmer's block-pass helpers; the two
 * effects' rings are the same size, so SH_MASK is PL_MASK).
 *
 * The algorithm is the plate reverb Jon Dattorro published in "Effect Design, Part 1:
 * Reverberator and Other Filters" (J. Audio Eng. Soc., 1997), written from the paper:
 * the send, summed to mono and delayed by the Predelay, goes through a bandwidth lowpass
 * and four input allpass diffusers into a "tank" of two cross-coupled halves, each a
 * modulated allpass, a delay, a damping lowpass, the decay gain, a second allpass and a
 * second delay, the end of each half feeding the start of the other. The left and right
 * outputs are each the sum of seven taps spread over the tank's delays, as in the
 * paper's table.
 *
 * The paper's delay lengths are for 29.761 kHz; at 44.1 kHz they would be 1.48 times
 * longer. The stock reverb's two 32 KB buffers (object +0x24, +0x28; 32,768 16-bit
 * samples in all) don't hold that and the Predelay line too, so the lengths here are
 * 1.2 times the paper's: the same structure and taps, a plate about a fifth smaller.
 * The input diffusers and the left half of the tank are in the first buffer, the right
 * half in the second, and the Predelay in what is left of the two: a line at the end of
 * each, one after the other. The lines are stored as 16-bit samples in sample units, as
 * Freeverb's. As in Mutable Instruments' FxEngine, the lines of each buffer share one
 * ring of 16,384 samples (the whole buffer) with one write position that moves back a
 * sample at a time: line i writes at position + base_i and finds what it wrote k samples
 * ago at position + base_i + k, so every access is a constant offset and a mask, with no
 * per-line positions.
 *
 * The Master Reverb knobs:
 *  - Size (+0x54, 0..1)          -> decay 0.35..0.97 (the second tank allpasses follow
 *                                   it, decay + 0.15 within 0.25..0.5, as in the paper)
 *  - Damping (+0x5c = 1 - damping) -> the tank's damping lowpass, 0..0.85
 *  - Predelay (+0x6c, 0..5000 samples) -> a delay before the reverb
 *  - Diffusion (+0x58, 0..0.85)  -> the input diffusers (1.2 x diffusion and 0.83 times
 *                                   that) and the first tank allpasses (0.5 + 0.32 x
 *                                   diffusion); the stock default 0.625 gives the paper's
 *                                   0.75, 0.625 and 0.70
 * The modulated allpasses move by up to 19 samples at 1 Hz (one sine, one cosine). The
 * return is mixed as the stock reverb's: send + (wet - send) * amount, with the object's
 * amount (+0x4c) and input gain (+0x50); the bypass flag and the level field (+0x70) as
 * for Freeverb.
 *
 * The state (struct plate_state) is in .mt_ram, placed by the patcher.
 */
#ifndef PLATE_DSP_H
#define PLATE_DSP_H

#include <stdint.h>

/* ---- the plate's lines (lengths 1.2 x the paper's) --------------------------------- */
#define PL_EXCURSION        19u             /* the modulated allpasses' swing, samples */
#define PL_AP_A_LEN         806u            /* nominal lengths of the modulated allpasses */
#define PL_AP_B_LEN         1090u

/* line lengths; each takes len + 1 places in its ring */
#define L_IN1               170u
#define L_IN2               128u
#define L_IN3               455u
#define L_IN4               332u
#define L_AP_A              (PL_AP_A_LEN + PL_EXCURSION + 2u)
#define L_D1_A              5344u
#define L_AP2_A             2160u
#define L_D2_A              4464u
#define L_AP_B              (PL_AP_B_LEN + PL_EXCURSION + 2u)
#define L_D1_B              5060u
#define L_AP2_B             3187u
#define L_D2_B              3796u
/* ring 0 (the first buffer): the input diffusers and the left half */
#define B_IN1               0u
#define B_IN2               (B_IN1 + L_IN1 + 1u)
#define B_IN3               (B_IN2 + L_IN2 + 1u)
#define B_IN4               (B_IN3 + L_IN3 + 1u)
#define B_AP_A              (B_IN4 + L_IN4 + 1u)
#define B_D1_A              (B_AP_A + L_AP_A + 1u)
#define B_AP2_A             (B_D1_A + L_D1_A + 1u)
#define B_D2_A              (B_AP2_A + L_AP2_A + 1u)
#define B_PRE0              (B_D2_A + L_D2_A + 1u)    /* the first part of the predelay */
#define L_PRE0              (BUFFER_SAMPLES - B_PRE0 - 1u)
/* ring 1 (the second buffer): the right half */
#define B_AP_B              0u
#define B_D1_B              (B_AP_B + L_AP_B + 1u)
#define B_AP2_B             (B_D1_B + L_D1_B + 1u)
#define B_D2_B              (B_AP2_B + L_AP2_B + 1u)
#define B_PRE1              (B_D2_B + L_D2_B + 1u)    /* the rest of the predelay */
#define L_PRE1              (BUFFER_SAMPLES - B_PRE1 - 1u)
#define PL_MASK             (BUFFER_SAMPLES - 1u)     /* each ring: the whole 16,384-sample buffer */
#if B_PRE0 + 1u > BUFFER_SAMPLES || B_PRE1 + 1u > BUFFER_SAMPLES || L_PRE0 + L_PRE1 < PREDELAY_MAX
#error "the plate's lines and the predelay don't fit the two buffers"
#endif

#define PL_BANDWIDTH        0.9995f         /* the input lowpass: y = bw x + (1 - bw) y */
#define PL_TAP_GAIN         0.6f
#define PL_IN_GAIN          0.5f            /* the send into the plate, at the stock input gain 0.1 */
#define PL_OUT_TRIM         0.5f            /* the plate's return level against the stock reverb's */
/* the modulation's rotation per block: cos, sin of 2 pi 1 Hz 128 / 44100 */
#define PL_ROT_C            0.99983371f
#define PL_ROT_S            0.01823676f

#define PL_MAGIC            0x54414C50u     /* "PLAT": laid out for buf_l/buf_r */

struct plate_state {
    uint32_t magic;
    uint32_t buf_l, buf_r;
    uint32_t wp;                            /* the rings' write position */
    float bw;                               /* bandwidth lowpass state */
    float damp[2];                          /* the tank halves' damping lowpass states */
    float osc[2];                           /* cos, sin of the modulation */
};
static struct plate_state plate_state_ram __attribute__((section(".mt_ram")));
#define PL (&plate_state_ram)

/* the sample at `off` past the write position, in ring 0 or ring 1 */
#define PL_RD0(off)         ((float)q0[(wp + (off)) & PL_MASK])
#define PL_RD1(off)         ((float)q1[(wp + (off)) & PL_MASK])
#define PL_WR0(off, v)      (q0[(wp + (off)) & PL_MASK] = sat16(v))
#define PL_WR1(off, v)      (q1[(wp + (off)) & PL_MASK] = sat16(v))

/* Lay out the rings in the two (cleared) buffers. */
__attribute__((unused)) static void plate_init(uint32_t buf_l, uint32_t buf_r)
{
    PL->wp = 0;
    PL->bw = 0.0f;
    PL->damp[0] = PL->damp[1] = 0.0f;
    PL->osc[0] = 1.0f;
    PL->osc[1] = 0.0f;
    PL->buf_l = buf_l;
    PL->buf_r = buf_r;
    PL->magic = PL_MAGIC;
}

/* Process one block of the stereo send in place; returns the average output level. */
static float plate_process(uint8_t *obj, int16_t *left, int16_t *right)
{
    int16_t *q0 = (int16_t *)PL->buf_l, *q1 = (int16_t *)PL->buf_r;
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
    float dd1 = 0.5f + 0.32f * diffusion;
    float id1 = fclamp(1.2f * diffusion, 0.0f, 0.9f), id2 = id1 * 0.833f;
    float damp = 0.85f * damping, keep = 1.0f - damp;
    float in_gain = PL_IN_GAIN * (gain / 0.1f);
    const float tg = PL_TAP_GAIN * PL_OUT_TRIM;

    /* the modulation, from the start to the end of the block */
    float c = PL->osc[0], s = PL->osc[1];
    float c2 = c * PL_ROT_C - s * PL_ROT_S, s2 = s * PL_ROT_C + c * PL_ROT_S;
    float g = 1.5f - 0.5f * (c2 * c2 + s2 * s2);
    c2 *= g;
    s2 *= g;
    PL->osc[0] = c2;
    PL->osc[1] = s2;
    float sa = s * (float)PL_EXCURSION, sa_step = (s2 - s) * (float)PL_EXCURSION / BLOCK;
    float ca = c * (float)PL_EXCURSION, ca_step = (c2 - c) * (float)PL_EXCURSION / BLOCK;

    /* the predelay: up to L_PRE0 samples in ring 0, the rest in ring 1 */
    uint32_t pre0 = (uint32_t)predelay < L_PRE0 ? (uint32_t)predelay : L_PRE0;
    uint32_t pre1 = (uint32_t)predelay - pre0;

    float bw = PL->bw, lp_a = PL->damp[0], lp_b = PL->damp[1];
    uint32_t wp = PL->wp;

    /* the output taps for the whole block (the paper's table, x 1.2: line, samples
     * back): every one at least a block back, so none sees this block's writes. They
     * run as integer block passes (shimmer_dsp.h's helpers); the gains are the taps'
     * signs, and the integer sums convert exactly (7 x 32767 fits a float). */
    int32_t tapl[BLOCK], tapr[BLOCK];
    sh_pass4(tapl, 1,
             q1, (wp + B_D1_B + 319u) & PL_MASK, 1, q1, (wp + B_D1_B + 3569u) & PL_MASK, 1,
             q1, (wp + B_AP2_B + 2296u) & PL_MASK, -1, q1, (wp + B_D2_B + 2395u) & PL_MASK, 1);
    sh_pass3(tapl,
             q0, (wp + B_D1_A + 2388u) & PL_MASK, -1, q0, (wp + B_AP2_A + 224u) & PL_MASK, -1,
             q0, (wp + B_D2_A + 1279u) & PL_MASK, -1);
    sh_pass4(tapr, 1,
             q0, (wp + B_D1_A + 424u) & PL_MASK, 1, q0, (wp + B_D1_A + 4352u) & PL_MASK, 1,
             q0, (wp + B_AP2_A + 1474u) & PL_MASK, -1, q0, (wp + B_D2_A + 3208u) & PL_MASK, 1);
    sh_pass3(tapr,
             q1, (wp + B_D1_B + 2533u) & PL_MASK, -1, q1, (wp + B_AP2_B + 402u) & PL_MASK, -1,
             q1, (wp + B_D2_B + 145u) & PL_MASK, -1);

    float level = 0.0f;
    for (uint32_t n = 0; n < BLOCK; n++) {
        /* the predelay, in integers (the lines hold int16; / 2 cuts the half toward
         * zero, as the old float write's sat16 did) */
        int32_t xi = ((int32_t)left[n] + (int32_t)right[n]) / 2;
        q0[(wp + B_PRE0) & PL_MASK] = (int16_t)xi;
        if (pre0)
            xi = q0[(wp + B_PRE0 + pre0) & PL_MASK];
        q1[(wp + B_PRE1) & PL_MASK] = (int16_t)xi;
        if (pre1)
            xi = q1[(wp + B_PRE1 + pre1) & PL_MASK];
        float x = (float)xi * in_gain;

        float out_l = (float)tapl[n];
        float out_r = (float)tapr[n];
        float end_a = PL_RD0(B_D2_A + L_D2_A), end_b = PL_RD1(B_D2_B + L_D2_B);

        /* input: bandwidth, then the four diffusers (allpass: y = d - g x, the line gets x + g y) */
        bw = PL_BANDWIDTH * x + (1.0f - PL_BANDWIDTH) * bw;
        float v = bw, d, y;
        d = PL_RD0(B_IN1 + L_IN1); y = d - id1 * v; PL_WR0(B_IN1, v + id1 * y); v = y;
        d = PL_RD0(B_IN2 + L_IN2); y = d - id1 * v; PL_WR0(B_IN2, v + id1 * y); v = y;
        d = PL_RD0(B_IN3 + L_IN3); y = d - id2 * v; PL_WR0(B_IN3, v + id2 * y); v = y;
        d = PL_RD0(B_IN4 + L_IN4); y = d - id2 * v; PL_WR0(B_IN4, v + id2 * y); v = y;

        /* the tank, left half: modulated allpass (coefficient -dd1), delay, damping,
         * decay, allpass, delay */
        float in_a = v + decay * end_b;
        float dl = (float)PL_AP_A_LEN + sa;
        uint32_t whole = (uint32_t)dl;
        float frac = dl - (float)whole;
        d = PL_RD0(B_AP_A + whole) * (1.0f - frac) + PL_RD0(B_AP_A + whole + 1u) * frac;
        y = d + dd1 * in_a;
        PL_WR0(B_AP_A, in_a - dd1 * y);
        float a = PL_RD0(B_D1_A + L_D1_A);
        PL_WR0(B_D1_A, y);
        lp_a = keep * a + damp * lp_a;
        float u = lp_a * decay;
        d = PL_RD0(B_AP2_A + L_AP2_A); y = d - dd2 * u; PL_WR0(B_AP2_A, u + dd2 * y);
        PL_WR0(B_D2_A, y);

        /* the right half */
        float in_b = v + decay * end_a;
        dl = (float)PL_AP_B_LEN + ca;
        whole = (uint32_t)dl;
        frac = dl - (float)whole;
        d = PL_RD1(B_AP_B + whole) * (1.0f - frac) + PL_RD1(B_AP_B + whole + 1u) * frac;
        y = d + dd1 * in_b;
        PL_WR1(B_AP_B, in_b - dd1 * y);
        float b = PL_RD1(B_D1_B + L_D1_B);
        PL_WR1(B_D1_B, y);
        lp_b = keep * b + damp * lp_b;
        u = lp_b * decay;
        d = PL_RD1(B_AP2_B + L_AP2_B); y = d - dd2 * u; PL_WR1(B_AP2_B, u + dd2 * y);
        PL_WR1(B_D2_B, y);

        sa += sa_step;
        ca += ca_step;
        wp = (wp - 1u) & PL_MASK;

        float wl = out_l * tg, wrt = out_r * tg;
        float dry_l = (float)left[n], dry_r = (float)right[n];
        left[n] = sat16(dry_l + (wl - dry_l) * amount);
        right[n] = sat16(dry_r + (wrt - dry_r) * amount);
        level += (wl < 0 ? -wl : wl) + (wrt < 0 ? -wrt : wrt);
    }
    PL->wp = wp;
    PL->bw = bw;
    PL->damp[0] = lp_a;
    PL->damp[1] = lp_b;
    return level / BLOCK;
}

__attribute__((unused)) static void plate_blocks(uint8_t *obj)
{
    uint8_t *left = ((receive_fn)FW_RECEIVE_WRITABLE)(obj, 0);
    if (!left)
        return;
    uint8_t *right = ((receive_fn)FW_RECEIVE_WRITABLE)(obj, 1);
    if (!right) {
        ((release_fn)FW_RELEASE)(left);
        return;
    }
    *(volatile float *)(obj + RV_LEVEL) = plate_process(obj, (int16_t *)(left + 4), (int16_t *)(right + 4));
    ((transmit_fn)FW_TRANSMIT)(obj, left, 0);
    ((transmit_fn)FW_TRANSMIT)(obj, right, 1);
    ((release_fn)FW_RELEASE)(left);
    ((release_fn)FW_RELEASE)(right);
}

#endif
