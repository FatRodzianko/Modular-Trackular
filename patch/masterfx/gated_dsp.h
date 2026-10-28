/*
 * gated_dsp.h: a "gated" reverb on the master reverb object (Polyend Tracker+ firmware
 * 1.2.0), for masterfx.c. Include it once, in one .c file, after freeverb_dsp.h (object
 * fields, block helpers) and tape_dsp.h (POLE_MIN/POLE_MAX).
 *
 * Not a reverb behind a noise gate, but what the classic digital units (the AMS RMX16's
 * "non-linear" programs) did: a dense burst of reflections that lasts exactly as long as
 * set and then stops. It appears twice in the Reverb list: "Gated", and "Reverse", the
 * same burst swelling up over its length and then cutting off. (It sat in the Delay list
 * before mf7; in the Reverb list the two heaviest effects, Shimmer and this, can no
 * longer run at once, which caps the master-FX worst case. The reverb object's buffers
 * are 32 KB against the delay's 512 KB, so the gate now spans 10-368 ms - the classic
 * units' range - instead of 10 ms-2 s.)
 *
 *  - The mono send ((left + right) / 2, scaled by the Master Reverb input gain) is
 *    smeared by three short allpass diffusers (37, 53 and 71 samples) and written into a
 *    delay line (the reverb object's first buffer); 20 taps per side, spread unevenly
 *    over the gate length (different on each side), add up the burst (in integers, with
 *    Q14 gains, five taps at a time); each side is smeared again by three short
 *    allpasses (89, 131 and 173 samples left, 97, 149 and 197 right) into a dense wash.
 *    The diffusers are kept short and light so the burst still stops sharply: they ring
 *    on for well under 50 ms.
 *  - Each tap's level follows the gate's shape over the gate length: a 50 ms attack (or
 *    a quarter of the length, if shorter), a hold, and a release at the end, then
 *    nothing.
 *
 * The Master Reverb knobs:
 *  - Size (+0x54)      -> the gate length, 10 ms at 0 to 368 ms at 1
 *  - Damping (1 - +0x5c) -> the tone, a one-pole lowpass on the output
 *  - Diffusion (+0x58) -> the release: from a hard cut at 0 to a fade over the whole
 *                         length at the top
 *  - Predelay (+0x6c)  -> delays the burst (the gate shortens if both are at the top:
 *                         length + predelay fit the 16,384-sample line together)
 *  - Amount (+0x4c) and the input gain (+0x50) as for the other reverbs; reverse comes
 *    from the menu choice ("Reverse"), not a knob
 *  - the bypass flag (+0x80) as for the stock reverb (checked by the dispatch)
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
#define GT_LINE             BUFFER_SAMPLES  /* the tap line: the whole first buffer */
#define GT_LMASK            (GT_LINE - 1u)
#define GT_MIN              441u            /* gate length, samples: 10 ms .. */
#define GT_MAX              (GT_LINE - BLOCK)   /* .. 368 ms: the oldest tap plus this
                                             * block's writes fill the line exactly */
#define GT_ATTACK           2205.0f         /* 50 ms */
#define GT_TAP_GAIN         0.2236f         /* 1 / sqrt(GT_TAPS): the burst at about the send's level */
#define GT_Q                16384.0f        /* the taps' gains in Q14 (20 x 32768 x 0.22 x 16384 fits 32 bits) */
#define GT_DIFFUSE_Q        19661           /* the send's diffusers: 0.6 in Q15 (0.600006);
                                             * each side's diffusers are 0.5, a plain / 2 */

/* the allpasses: one ring (the first 4 KB of the second buffer) with a moving write
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
/* The allpasses share one small ring rather than sliding through the whole buffer: the
 * same samples come back, but the working set stays cache-sized on the device. It must
 * hold every line. */
#define GT_AP_RING          2048u
#define GT_AP_MASK          (GT_AP_RING - 1u)
_Static_assert(GB_R3 + GL_R3 + 1u <= GT_AP_RING, "the allpass lines fit their ring");
_Static_assert(GT_AP_RING <= BUFFER_SAMPLES, "the ring fits the second buffer");

#define GT_MAGIC            0x45544147u     /* "GATE": laid out for buf[] */

struct gated_state {
    uint32_t magic;
    uint32_t buf[2];                        /* the tap line, and the allpasses' ring */
    uint32_t wpos;                          /* the tap line's write position */
    uint32_t wp;                            /* the allpasses' ring position */
    float lp_x[2], lp_y[2];                 /* the Damping knob's lowpass */
};
static struct gated_state gated_state_ram __attribute__((section(".mt_ram")));
#define GT (&gated_state_ram)

#define GT_RD(off)          ((int32_t)ap[(wp + (off)) & GT_AP_MASK])
#define GT_WR(off, v)       (ap[(wp + (off)) & GT_AP_MASK] = sat16i(v))

static inline int16_t sat16i(int32_t v)
{
    return (int16_t)__ssat(v, 16);
}

static inline int32_t gt_diffuse(int32_t v) /* v x 0.6, Q15, rounded toward zero: the
                                             * allpasses quantize inside their feedback,
                                             * and magnitude truncation is what keeps a
                                             * +-1 from circling forever (a limit cycle)
                                             * once the input stops */
{
    return (v * GT_DIFFUSE_Q) / 32768;
}

/* The tap table, cached between blocks: rebuilding it (40 gains, with a divide each)
 * costs about 1,200 instructions a block, and the knobs rarely move. It lives in the
 * patch's late RAM block (tools/patch_ptf.py "late_ram"; the word holds the block's
 * size as linked and its address once patched), which may be SDRAM, so it is only
 * touched from the audio path, after the unit has booted. The block starts as garbage:
 * the magic says the table matches the keys, and gated_init() clears it. */
#define GT_CACHE_MAGIC      0x32544147u     /* "GAT2": the reverb-slot layout */

struct gated_cache {
    uint32_t magic;
    uint32_t key_len;                       /* the clamped gate length the table is for */
    uint32_t key_pre;                       /* ... and the predelay */
    uint8_t key_rel, key_rev;               /* ... the release byte and reverse flag */
    uint16_t pad;
    uint32_t back[2][GT_TAPS];              /* each tap: samples back ... */
    int32_t gain[2][GT_TAPS];               /* ... and its gain, Q14 */
};
const volatile uint32_t mt_mfx_late_ram __attribute__((used)) = sizeof(struct gated_cache);
#define GTC ((struct gated_cache *)mt_mfx_late_ram)

/* Lay out the lines in the (cleared) buffers. */
__attribute__((unused)) static void gated_init(uint32_t buf_l, uint32_t buf_r)
{
    GT->buf[0] = buf_l;
    GT->buf[1] = buf_r;
    GT->wpos = 0;
    GT->wp = 0;
    GT->lp_x[0] = GT->lp_x[1] = GT->lp_y[0] = GT->lp_y[1] = 0.0f;
    GTC->magic = 0;
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

/* Run the burst over one block: the stereo send in place, as the other reverbs;
 * returns the average wet level for the object's level field. */
static float gated_process(const uint8_t *obj, int16_t *left, int16_t *right, int reverse)
{
    int16_t *line = (int16_t *)GT->buf[0], *ap = (int16_t *)GT->buf[1];
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
    uint32_t pre = (uint32_t)predelay & ~1u; /* whole pairs, as the taps */
    uint32_t len = GT_MIN + (uint32_t)(size * (float)(GT_MAX - GT_MIN));
    if (len + pre > GT_MAX)
        len = GT_MAX - pre;                 /* both at the top: the line holds len + pre */
    /* the release byte, as the old Feedback knob: Diffusion's 0..0.85 spans it */
    float rel = diffusion * (1.0f / 0.85f);
    if (rel > 1.0f)
        rel = 1.0f;
    uint8_t rel_byte = (uint8_t)(rel * 252.0f + 0.5f);
    /* Damping sweeps the output pole over the old Filter knob's whole range */
    float pole = POLE_MIN + damping * (POLE_MAX - POLE_MIN);
    float lp_b = 0.5f * (1.0f - pole);
    int32_t in_q = (int32_t)(gain * 2.0f * 16384.0f);   /* x 2 x gain, Q14 */

    /* the taps for this length: where they are and how loud (cached; rebuilt when a
     * knob that shapes them moved) */
    struct gated_cache *gc = GTC;
    if (gc->magic != GT_CACHE_MAGIC || gc->key_len != len || gc->key_pre != pre
        || gc->key_rel != rel_byte || gc->key_rev != reverse) {
        float fb = (float)rel_byte * (1.0f / 252.0f);
        if (fb > 1.0f)
            fb = 1.0f;
        float attack = GT_ATTACK / (float)len;
        if (attack > 0.25f)
            attack = 0.25f;
        float release = fb * (1.0f - attack);
        for (uint32_t s = 0; s < 2; s++)
            for (uint32_t k = 0; k < GT_TAPS; k++) {
                /* whole pairs only (so the burst loop's word reads stay aligned) */
                uint32_t d = (uint32_t)(gt_pos[s][k] * (float)len) & ~1u;
                gc->back[s][k] = (d ? d : 2u) + pre;
                gc->gain[s][k] = (int32_t)(GT_Q * GT_TAP_GAIN * gt_shape(gt_pos[s][k], attack, release, reverse));
            }
        gc->key_len = len;
        gc->key_pre = pre;
        gc->key_rel = rel_byte;
        gc->key_rev = (uint8_t)reverse;
        gc->magic = GT_CACHE_MAGIC;
    }

    uint32_t pos0 = GT->wpos, wp0 = GT->wp, pos = pos0, wp = wp0;
    int32_t d, y;
    /* 1. the send, diffused, onto the tap line (integers: the lines hold int16 anyway,
     * and the diffusion is Q15, so nothing converts to float and back) */
    for (uint32_t n = 0; n < BLOCK; n++) {
        int32_t v = (((int32_t)left[n] + (int32_t)right[n]) / 2 * in_q) >> 14;
        d = GT_RD(GB_PRE1 + GL_PRE1); y = d - gt_diffuse(v); GT_WR(GB_PRE1, v + gt_diffuse(y)); v = y;
        d = GT_RD(GB_PRE2 + GL_PRE2); y = d - gt_diffuse(v); GT_WR(GB_PRE2, v + gt_diffuse(y)); v = y;
        d = GT_RD(GB_PRE3 + GL_PRE3); y = d - gt_diffuse(v); GT_WR(GB_PRE3, v + gt_diffuse(y)); v = y;
        line[pos] = sat16i(v);
        pos = (pos + 1u) & GT_LMASK;
        wp = (wp - 1u) & GT_AP_MASK;
    }
    /* 2. the burst: five taps at a time over the block, two samples at a time (each
     * tap's samples are in a row; the pass is split where one of the five wraps). The
     * taps are whole pairs back, the write position moves a whole block (128) at a
     * time from 0, and the wrap points are even, so every word read here is aligned:
     * one 32-bit read is two samples, and each gain MACs them as smlabb/smlatb. The
     * samples this block wrote are already there. */
    _Static_assert(BLOCK % 2u == 0 && GT_LINE % 2u == 0, "the burst reads sample pairs");
    int32_t acc[2][BLOCK];
    for (uint32_t s = 0; s < 2; s++) {
        int32_t *a = acc[s];
        for (uint32_t grp = 0; grp < GT_TAPS; grp += GT_GROUP) {
            uint32_t start[GT_GROUP];
            int32_t g[GT_GROUP];
            for (uint32_t j = 0; j < GT_GROUP; j++) {
                start[j] = (pos0 - gc->back[s][grp + j]) & GT_LMASK;
                g[j] = gc->gain[s][grp + j];
            }
            for (uint32_t n = 0; n < BLOCK;) {
                uint32_t run = BLOCK - n;
                for (uint32_t j = 0; j < GT_GROUP; j++)
                    if (GT_LINE - start[j] < run)
                        run = GT_LINE - start[j];
                const uint32_t *q0 = (const uint32_t *)(line + start[0]), *q1 = (const uint32_t *)(line + start[1]);
                const uint32_t *q2 = (const uint32_t *)(line + start[2]), *q3 = (const uint32_t *)(line + start[3]);
                const uint32_t *q4 = (const uint32_t *)(line + start[4]);
                /* both MAC operands int16, so each gain x pair is one smlabb + smlatb */
                int16_t g0 = (int16_t)g[0], g1 = (int16_t)g[1], g2 = (int16_t)g[2];
                int16_t g3 = (int16_t)g[3], g4 = (int16_t)g[4];
                int32_t *o = a + n;
                int32_t *end = o + run;
                if (grp == 0)
                    for (; o != end; o += 2) {
                        uint32_t w0 = *q0++, w1 = *q1++, w2 = *q2++, w3 = *q3++, w4 = *q4++;
                        int32_t a0 = (int16_t)w0 * g0, a1 = (int16_t)(w0 >> 16) * g0;
                        a0 += (int16_t)w1 * g1;
                        a1 += (int16_t)(w1 >> 16) * g1;
                        a0 += (int16_t)w2 * g2;
                        a1 += (int16_t)(w2 >> 16) * g2;
                        a0 += (int16_t)w3 * g3;
                        a1 += (int16_t)(w3 >> 16) * g3;
                        a0 += (int16_t)w4 * g4;
                        a1 += (int16_t)(w4 >> 16) * g4;
                        o[0] = a0;
                        o[1] = a1;
                    }
                else
                    for (; o != end; o += 2) {
                        uint32_t w0 = *q0++, w1 = *q1++, w2 = *q2++, w3 = *q3++, w4 = *q4++;
                        int32_t a0 = o[0] + (int16_t)w0 * g0, a1 = o[1] + (int16_t)(w0 >> 16) * g0;
                        a0 += (int16_t)w1 * g1;
                        a1 += (int16_t)(w1 >> 16) * g1;
                        a0 += (int16_t)w2 * g2;
                        a1 += (int16_t)(w2 >> 16) * g2;
                        a0 += (int16_t)w3 * g3;
                        a1 += (int16_t)(w3 >> 16) * g3;
                        a0 += (int16_t)w4 * g4;
                        a1 += (int16_t)(w4 >> 16) * g4;
                        o[0] = a0;
                        o[1] = a1;
                    }
                for (uint32_t j = 0; j < GT_GROUP; j++)
                    start[j] = (start[j] + run) & GT_LMASK;
                n += run;
            }
        }
    }
    /* 3. each side smeared into a wash (integers; the smear 0.5 is a shift), then the
     * tone, still float (two values a sample, and the pole wants the precision), then
     * the Amount mix into the send blocks */
    wp = wp0;
    float lpx0 = GT->lp_x[0], lpy0 = GT->lp_y[0], lpx1 = GT->lp_x[1], lpy1 = GT->lp_y[1];
    float level = 0.0f;
    for (uint32_t n = 0; n < BLOCK; n++) {
        int32_t l = (acc[0][n] + (1 << 13)) >> 14, r = (acc[1][n] + (1 << 13)) >> 14;   /* / GT_Q */
        d = GT_RD(GB_L1 + GL_L1); y = d - l / 2; GT_WR(GB_L1, l + y / 2); l = y;
        d = GT_RD(GB_L2 + GL_L2); y = d - l / 2; GT_WR(GB_L2, l + y / 2); l = y;
        d = GT_RD(GB_L3 + GL_L3); y = d - l / 2; GT_WR(GB_L3, l + y / 2); l = y;
        d = GT_RD(GB_R1 + GL_R1); y = d - r / 2; GT_WR(GB_R1, r + y / 2); r = y;
        d = GT_RD(GB_R2 + GL_R2); y = d - r / 2; GT_WR(GB_R2, r + y / 2); r = y;
        d = GT_RD(GB_R3 + GL_R3); y = d - r / 2; GT_WR(GB_R3, r + y / 2); r = y;
        lpy0 = lp_b * ((float)l + lpx0) + pole * lpy0;
        lpx0 = (float)l;
        lpy1 = lp_b * ((float)r + lpx1) + pole * lpy1;
        lpx1 = (float)r;
        float dl = (float)left[n], dr = (float)right[n];
        left[n] = sat16(dl + (lpy0 - dl) * amount);
        right[n] = sat16(dr + (lpy1 - dr) * amount);
        level += (lpy0 < 0.0f ? -lpy0 : lpy0) + (lpy1 < 0.0f ? -lpy1 : lpy1);
        wp = (wp - 1u) & GT_AP_MASK;
    }
    GT->wpos = pos;
    GT->wp = wp;
    GT->lp_x[0] = lpx0;
    GT->lp_y[0] = lpy0;
    GT->lp_x[1] = lpx1;
    GT->lp_y[1] = lpy1;
    return level / BLOCK;
}

/* Take the stereo send, run the burst in place, send the returns, as the stock
 * update() does (the dispatch has already checked the bypass flag). */
__attribute__((unused)) static void gated_blocks(uint8_t *obj, int reverse)
{
    uint8_t *left = ((receive_fn)FW_RECEIVE_WRITABLE)(obj, 0);
    if (!left)
        return;
    uint8_t *right = ((receive_fn)FW_RECEIVE_WRITABLE)(obj, 1);
    if (!right) {
        ((release_fn)FW_RELEASE)(left);
        return;
    }
    *(volatile float *)(obj + RV_LEVEL) = gated_process(obj, (int16_t *)(left + 4), (int16_t *)(right + 4), reverse);
    ((transmit_fn)FW_TRANSMIT)(obj, left, 0);
    ((transmit_fn)FW_TRANSMIT)(obj, right, 1);
    ((release_fn)FW_RELEASE)(left);
    ((release_fn)FW_RELEASE)(right);
}

#endif
