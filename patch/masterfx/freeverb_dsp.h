/*
 * freeverb_dsp.h: Freeverb on the master reverb object (Polyend Tracker+ firmware 1.2.0),
 * for masterfx.c. Include it once, in one .c file.
 *
 * Freeverb (Jezar at Dreampoint, public domain): the send is summed to mono and fed to
 * 8 parallel comb filters with a one-pole lowpass in their feedback, then 4 series
 * allpasses, per channel; the right channel's delays are 23 samples longer (stereo
 * spread). The delay lengths are Freeverb's own, for 44.1 kHz. The delay lines are
 * stored as 16-bit samples in the stock reverb's two 32 KB SDRAM buffers (object +0x24
 * and +0x28): left in the first, right in the second, and the predelay line in what is
 * left at the end of the two. fv_init() lays them out, clear_step() clears the buffers a
 * piece at a time, and freeverb_blocks() takes the two send blocks, runs Freeverb on them
 * in place and transmits them, as the stock update() does. The state (struct fv_state,
 * 288 bytes) is in .mt_ram, placed by the patcher.
 *
 * The filters run over the whole block at a time, in sample units (16-bit full scale =
 * 32768), so the delay lines need no scaling: the combs four at a time (adding their
 * outputs in the same order as one by one, so the result is the same to the bit), the
 * four allpasses together, and each pass split where one of its lines wraps, so the
 * inner loops have no per-sample position checks.
 *
 * The Master Reverb page is unchanged; its setters still write the reverb object, and
 * Freeverb reads those fields every block:
 *  - Size (+0x54, 0..1)       -> room size: comb feedback 0.70..0.98
 *  - Damping (+0x5c = 1 - damping) -> damping of the comb lowpass, 0..0.4
 *  - Predelay (+0x6c, 0..5000 samples, up to about 113 ms) -> a delay before the reverb
 *  - Diffusion (+0x58, 0..0.85) -> allpass feedback 0.30..0.72
 * The return is mixed as the stock reverb mixes it: send + (wet - send) * amount, with
 * the object's amount (+0x4c, 0.8) and input gain (+0x50, 0.1). The block's average
 * output level goes to +0x70, as the stock reverb writes it.
 */
#ifndef FREEVERB_DSP_H
#define FREEVERB_DSP_H

#include <stdint.h>
#include <arm_acle.h>                      /* __ssat */

/* ---- firmware 1.2.0 (function pointers carry the Thumb bit) ---------------------- */
#define FW_RECEIVE_WRITABLE 0x600305D5u     /* (object, input) -> block or 0 */
#define FW_TRANSMIT         0x60030575u     /* (object, block, output) */
#define FW_RELEASE          0x60030525u     /* (block) */
#define FW_REVERB_UPDATE    0x60031E5Du     /* the stock update() */

/* reverb object fields */
#define RV_BUF_L            0x24u           /* -> int16[16384], SDRAM */
#define RV_BUF_R            0x28u           /* -> int16[16384], SDRAM */
#define RV_AMOUNT           0x4Cu           /* float: return = send + (wet - send) * amount */
#define RV_INPUT_GAIN       0x50u           /* float */
#define RV_SIZE             0x54u           /* float 0..1 (Size) */
#define RV_DIFFUSION        0x58u           /* float 0..0.85 (Diffusion) */
#define RV_LP               0x5Cu           /* float: 1 - Damping */
#define RV_PREDELAY         0x6Cu           /* int32 samples, 0..5000 (Predelay) */
#define RV_LEVEL            0x70u           /* float: average output level of the last block */
#define RV_BYPASS           0x80u           /* byte: nonzero = no processing, no output */

#define BLOCK               128u
#define BUFFER_SAMPLES      16384u          /* each stock buffer: 32 KB */

typedef void *(*receive_fn)(void *obj, uint32_t input);
typedef void (*transmit_fn)(void *obj, void *block, uint32_t output);
typedef void (*release_fn)(void *block);

/* ---- Freeverb tuning (44.1 kHz) ---------------------------------------------------- */
#define COMBS               8u
#define ALLPASSES           4u
#define STEREO_SPREAD       23u
#define FIXED_GAIN          0.015f          /* Freeverb's input gain ... */
#define SCALE_WET           3.0f            /* ... and wet gain */
#define LINE_GAIN           4.0f            /* the combs run this much hotter (16-bit lines) */
#define OUTPUT_TRIM         0.122f          /* -18.3 dB: the stock reverb's return level */
#define SCALE_ROOM          0.28f
#define OFFSET_ROOM         0.7f
#define SCALE_DAMP          0.4f
#define PREDELAY_MAX        5000u           /* the stock setter's range */
#define PRE_RING            (PREDELAY_MAX + 1u)

static const uint16_t comb_len[COMBS] = {1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617};
static const uint16_t allpass_len[ALLPASSES] = {556, 441, 341, 225};

/* ---- state (.mt_ram, placed by the patcher in on-chip RAM) ----------------------- */
#define FV_MAGIC            0x56524654u     /* "TFRV": lines laid out for buf_l/buf_r */
#define FV_READY            0x59444552u     /* "REDY": booted, Freeverb may run */
#define CLEAR_WORDS         1024u           /* words of each buffer cleared per block (8 blocks) */

struct line { uint16_t start, len, pos, pad; };

struct fv_state {
    uint32_t magic;
    uint32_t buf_l, buf_r;                  /* the buffers the lines were laid out in */
    struct line comb[2][COMBS];
    struct line allpass[2][ALLPASSES];
    float comb_store[2][COMBS];             /* comb lowpass states */
    uint16_t pre_pos;                       /* predelay ring write position, 0..PRE_RING-1 */
    uint16_t pre_split;                     /* ring positions below this are in the left buffer */
    uint32_t pre_l, pre_r;                  /* the ring's two parts: the buffers' unused ends */
    uint32_t ready;                         /* FV_READY once the unit has booted */
    uint32_t cleared;                       /* words of each buffer cleared so far */
};
static struct fv_state fv_state_ram __attribute__((section(".mt_ram")));
#define FV (&fv_state_ram)

static inline int16_t sat16(float v)
{
    return (int16_t)__ssat((int32_t)v, 16);
}

/* A predelay ring position: the first part is in the left buffer's spare end, the rest
 * in the right buffer's. */
static inline int16_t *pre_at(uint32_t i)
{
    return i < FV->pre_split ? (int16_t *)FV->pre_l + i : (int16_t *)FV->pre_r + (i - FV->pre_split);
}

/* Lay out the delay lines in the two buffers (cleared afterwards, a piece per block). */
__attribute__((unused)) static void fv_init(uint32_t buf_l, uint32_t buf_r)
{
    for (uint32_t ch = 0; ch < 2; ch++) {
        uint32_t at = 0, spread = ch ? STEREO_SPREAD : 0;
        for (uint32_t i = 0; i < COMBS; i++) {
            FV->comb[ch][i] = (struct line){(uint16_t)at, (uint16_t)(comb_len[i] + spread), 0, 0};
            at += comb_len[i] + spread;
            FV->comb_store[ch][i] = 0.0f;
        }
        for (uint32_t i = 0; i < ALLPASSES; i++) {
            FV->allpass[ch][i] = (struct line){(uint16_t)at, (uint16_t)(allpass_len[i] + spread), 0, 0};
            at += allpass_len[i] + spread;
        }
        /* the rest of this buffer: part of the predelay ring */
        if (ch == 0) {
            FV->pre_l = buf_l + 2 * at;
            FV->pre_split = (uint16_t)(BUFFER_SAMPLES - at);
        } else {
            FV->pre_r = buf_r + 2 * at;
        }
    }
    FV->cleared = 0;
    FV->pre_pos = 0;
    FV->buf_l = buf_l;
    FV->buf_r = buf_r;
    FV->magic = FV_MAGIC;
}

/* Clear the next piece of the two buffers; 1 when they are clear. */
__attribute__((unused)) static int clear_step(uint32_t words)
{
    volatile uint32_t *l = (volatile uint32_t *)FV->buf_l, *r = (volatile uint32_t *)FV->buf_r;
    uint32_t end = FV->cleared + words;
    if (end > BUFFER_SAMPLES / 2)
        end = BUFFER_SAMPLES / 2;
    for (uint32_t i = FV->cleared; i < end; i++) {
        l[i] = 0;
        r[i] = 0;
    }
    FV->cleared = end;
    return end == BUFFER_SAMPLES / 2;
}

#define GROUP               4u              /* combs per pass */

/* Samples until the first of n lines wraps, at most `left`. */
static inline uint32_t run_length(const uint32_t *pos, const uint32_t *len, uint32_t n, uint32_t left)
{
    for (uint32_t j = 0; j < n; j++)
        if (len[j] - pos[j] < left)
            left = len[j] - pos[j];
    return left;
}

/* Four comb filters over the block. first: acc = their sum, else acc += their sum, with
 * the outputs added in comb order as one comb per pass would. */
static inline __attribute__((always_inline)) void comb_group(struct line *c, int16_t *buf, float *store_p,
                                                             const float *in, float *acc, int first,
                                                             float feedback, float damp1, float damp2)
{
    uint32_t pos[GROUP], len[GROUP];
    for (uint32_t k = 0; k < GROUP; k++) {
        pos[k] = c[k].pos;
        len[k] = c[k].len;
    }
    float s0 = store_p[0], s1 = store_p[1], s2 = store_p[2], s3 = store_p[3];
    for (uint32_t n = 0; n < BLOCK;) {
        uint32_t run = run_length(pos, len, GROUP, BLOCK - n);
        int16_t *q0 = buf + c[0].start + pos[0], *q1 = buf + c[1].start + pos[1];
        int16_t *q2 = buf + c[2].start + pos[2], *q3 = buf + c[3].start + pos[3];
        const float *x = in + n;
        float *a = acc + n;
        for (uint32_t k = 0; k < run; k++) {
            float o0 = (float)q0[k], o1 = (float)q1[k], o2 = (float)q2[k], o3 = (float)q3[k];
            s0 = o0 * damp2 + s0 * damp1;
            q0[k] = sat16(x[k] + s0 * feedback);
            s1 = o1 * damp2 + s1 * damp1;
            q1[k] = sat16(x[k] + s1 * feedback);
            s2 = o2 * damp2 + s2 * damp1;
            q2[k] = sat16(x[k] + s2 * feedback);
            s3 = o3 * damp2 + s3 * damp1;
            q3[k] = sat16(x[k] + s3 * feedback);
            float sum = first ? 0.0f + o0 : a[k] + o0;
            a[k] = ((sum + o1) + o2) + o3;
        }
        for (uint32_t k = 0; k < GROUP; k++)
            if ((pos[k] += run) == len[k])
                pos[k] = 0;
        n += run;
    }
    for (uint32_t k = 0; k < GROUP; k++)
        c[k].pos = (uint16_t)pos[k];
    store_p[0] = s0;
    store_p[1] = s1;
    store_p[2] = s2;
    store_p[3] = s3;
}

/* The four allpasses in series over the block, in place. */
__attribute__((unused)) static void allpass_chain(struct line *a, int16_t *buf, float *io, float feedback)
{
    uint32_t pos[ALLPASSES], len[ALLPASSES];
    for (uint32_t k = 0; k < ALLPASSES; k++) {
        pos[k] = a[k].pos;
        len[k] = a[k].len;
    }
    for (uint32_t n = 0; n < BLOCK;) {
        uint32_t run = run_length(pos, len, ALLPASSES, BLOCK - n);
        int16_t *q0 = buf + a[0].start + pos[0], *q1 = buf + a[1].start + pos[1];
        int16_t *q2 = buf + a[2].start + pos[2], *q3 = buf + a[3].start + pos[3];
        float *v = io + n;
        for (uint32_t k = 0; k < run; k++) {
            float x = v[k], b;
            b = (float)q0[k];
            q0[k] = sat16(x + b * feedback);
            x = b - x;
            b = (float)q1[k];
            q1[k] = sat16(x + b * feedback);
            x = b - x;
            b = (float)q2[k];
            q2[k] = sat16(x + b * feedback);
            x = b - x;
            b = (float)q3[k];
            q3[k] = sat16(x + b * feedback);
            v[k] = b - x;
        }
        for (uint32_t k = 0; k < ALLPASSES; k++)
            if ((pos[k] += run) == len[k])
                pos[k] = 0;
        n += run;
    }
    for (uint32_t k = 0; k < ALLPASSES; k++)
        a[k].pos = (uint16_t)pos[k];
}


/* lo..hi; NaN (from a corrupted project) counts as lo, so filter states never get NaN */
static float fclamp(float v, float lo, float hi)
{
    if (!(v >= lo))
        return lo;
    return v > hi ? hi : v;
}

/* Process one block of the stereo send in place; returns the average output level. */
static float fv_process(uint8_t *obj, int16_t *left, int16_t *right, int muted)
{
    int16_t *buf[2] = {(int16_t *)FV->buf_l, (int16_t *)FV->buf_r};
    int16_t *io[2] = {left, right};
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

    float feedback = size * SCALE_ROOM + OFFSET_ROOM;
    float damp1 = damping * SCALE_DAMP, damp2 = 1.0f - damp1;
    float ap_feedback = 0.3f + diffusion * 0.5f;
    /* the stock reverb's input gain (0.1) moves Freeverb's input gain with it */
    float in_gain = FIXED_GAIN * LINE_GAIN * (gain / 0.1f);
    float wet_gain = SCALE_WET * OUTPUT_TRIM / LINE_GAIN;

    /* the mono send through the predelay (a ring of PRE_RING samples) */
    float mono[BLOCK];
    uint32_t wr = FV->pre_pos;
    for (uint32_t n = 0; n < BLOCK; n++) {
        *pre_at(wr) = sat16(((float)left[n] + (float)right[n]) * 0.5f);
        uint32_t rd = wr + PRE_RING - (uint32_t)predelay;
        if (rd >= PRE_RING)
            rd -= PRE_RING;
        mono[n] = (float)*pre_at(rd) * 2.0f * in_gain;
        if (++wr == PRE_RING)
            wr = 0;
    }
    FV->pre_pos = (uint16_t)wr;

    float level = 0.0f;
    for (uint32_t ch = 0; ch < 2; ch++) {
        float acc[BLOCK];
        comb_group(&FV->comb[ch][0], buf[ch], &FV->comb_store[ch][0], mono, acc, 1, feedback, damp1, damp2);
        comb_group(&FV->comb[ch][GROUP], buf[ch], &FV->comb_store[ch][GROUP], mono, acc, 0, feedback, damp1, damp2);
        allpass_chain(FV->allpass[ch], buf[ch], acc, ap_feedback);
        int16_t *s = io[ch];
        for (uint32_t n = 0; n < BLOCK; n++) {
            float wet = (float)acc[n] * wet_gain, dry = (float)s[n];
            if (!muted)
                s[n] = sat16(dry + (wet - dry) * amount);
            level += wet < 0 ? -wet : wet;
        }
    }
    return level / BLOCK;
}

/* Take the two send blocks, process them with Freeverb and send them on, as the stock
 * update() does. */
__attribute__((unused)) static void freeverb_blocks(uint8_t *obj, int muted)
{
    uint8_t *left = ((receive_fn)FW_RECEIVE_WRITABLE)(obj, 0);
    if (!left)
        return;
    uint8_t *right = ((receive_fn)FW_RECEIVE_WRITABLE)(obj, 1);
    if (!right) {
        ((release_fn)FW_RELEASE)(left);
        return;
    }
    *(volatile float *)(obj + RV_LEVEL) = fv_process(obj, (int16_t *)(left + 4), (int16_t *)(right + 4), muted);
    ((transmit_fn)FW_TRANSMIT)(obj, left, 0);
    ((transmit_fn)FW_TRANSMIT)(obj, right, 1);
    ((release_fn)FW_RELEASE)(left);
    ((release_fn)FW_RELEASE)(right);
}

#endif
