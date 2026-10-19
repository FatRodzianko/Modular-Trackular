/*
 * freeverb: Freeverb as the master reverb (Polyend Tracker+ firmware 1.2.0).
 *
 * The master reverb is one audio object (0x20019014, engine + 0x150c) in the firmware's
 * Teensy-style audio graph: once per audio block its update() (vtable 0x60042f94,
 * 0x60031e5d) takes the stereo reverb send (2 inputs of 128 16-bit samples), runs the
 * stock reverb on it in place and transmits it as the reverb return. mt_fv_update
 * replaces that update(): same blocks, same in-place processing, with Freeverb (Jezar
 * at Dreampoint, public domain) instead of the stock reverb.
 *
 * Freeverb: the send is summed to mono and fed to 8 parallel comb filters with a
 * one-pole lowpass in their feedback, then 4 series allpasses, per channel; the right
 * channel's delays are 23 samples longer (stereo spread). The delay lengths are
 * Freeverb's own, for 44.1 kHz. The delay lines are stored as 16-bit samples in the
 * stock reverb's two 32 KB SDRAM buffers (object +0x24 and +0x28), which the stock
 * reverb no longer uses: left in the first, right in the second, and the predelay line in
 * what is left at the end of the two. The patch clears them before first use. Its own
 * state (about 300 bytes) is on-chip: the top of SDRAM, free once the unit is running,
 * hung the boot when this patch used it from the audio interrupt. Each filter runs over the whole block at a time, in sample units
 * (16-bit full scale = 32768), so the delay lines need no scaling.
 *
 * The Master Reverb page is unchanged; its setters still write the reverb object, and
 * Freeverb reads those fields every block:
 *  - Size (+0x54, 0..1)       -> room size: comb feedback 0.70..0.98
 *  - Damping (+0x5c = 1 - damping) -> damping of the comb lowpass, 0..0.4
 *  - Predelay (+0x6c, 0..5000 samples, up to about 113 ms) -> a delay before the reverb
 *  - Diffusion (+0x58, 0..0.85) -> allpass feedback 0.30..0.72
 * The return is mixed as the stock reverb mixes it: send + (wet - send) * amount, with
 * the object's amount (+0x4c, 0.8) and input gain (+0x50, 0.1). The bypass flag (+0x80)
 * works as before, and the block's average output level goes to +0x70, as the stock
 * reverb writes it.
 *
 * Start-up. Freeverb only takes over once the unit has finished booting: until the
 * Pattern screen's update method first runs, mt_fv_update calls the stock update().
 * Then it clears the two buffers a piece per block (silent meanwhile) and switches to
 * Freeverb. The Config menu constructor (a static constructor, so at every start)
 * resets that state, so Freeverb never runs during a boot.
 *
 * Diagnostic builds:
 *  - -DFV_PASSTHROUGH ("fvP"): the hook only calls the stock update();
 *  - -DFV_DIAG_CLEAR ("fvC"): the stock update(), plus the whole 64 KB clear at once on
 *    the first block that has input, from boot on;
 *  - -DFV_DIAG_MUTED ("fvD"): Freeverb runs from boot on, without clearing, and its
 *    output is the send unchanged (the wet signal is computed but not used).
 */
#include <stdint.h>
#include <arm_acle.h>                      /* __ssat */

#ifndef FREEVERB_VERSION
#define FREEVERB_VERSION "fv4"
#endif

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
typedef void (*update_fn)(void *obj);
typedef void (*screen_update_fn)(void *screen, void *a1, void *a2, void *a3);
typedef void *(*item_fn)(void *node, void *parent, const char *label, const char *help,
                         uint32_t type, const void *descriptor);

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_fv_next_pattern_update);      /* Pattern screen update 0x600e1f99 */
CHAIN_WORD(mt_fv_next_last_item);           /* last item: cfg_menu_item 0x601183e9 */

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

/* One comb filter over the block: acc += its output. */
static void comb_block(struct line *c, int16_t *buf, float *store_p, const float *in, float *acc,
                       float feedback, float damp1, float damp2)
{
    int16_t *line = buf + c->start;
    uint32_t pos = c->pos, len = c->len;
    float store = *store_p;
    for (uint32_t n = 0; n < BLOCK; n++) {
        float out = (float)line[pos];
        store = out * damp2 + store * damp1;
        line[pos] = sat16(in[n] + store * feedback);
        acc[n] += out;
        if (++pos == len)
            pos = 0;
    }
    c->pos = (uint16_t)pos;
    *store_p = store;
}

/* One allpass over the block, in place. */
static void allpass_block(struct line *a, int16_t *buf, float *io, float feedback)
{
    int16_t *line = buf + a->start;
    uint32_t pos = a->pos, len = a->len;
    for (uint32_t n = 0; n < BLOCK; n++) {
        float in = io[n], bufout = (float)line[pos];
        line[pos] = sat16(in + bufout * feedback);
        io[n] = bufout - in;
        if (++pos == len)
            pos = 0;
    }
    a->pos = (uint16_t)pos;
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
        for (uint32_t n = 0; n < BLOCK; n++)
            acc[n] = 0.0f;
        for (uint32_t i = 0; i < COMBS; i++)
            comb_block(&FV->comb[ch][i], buf[ch], &FV->comb_store[ch][i], mono, acc, feedback, damp1, damp2);
        for (uint32_t i = 0; i < ALLPASSES; i++)
            allpass_block(&FV->allpass[ch][i], buf[ch], acc, ap_feedback);
        int16_t *s = io[ch];
        for (uint32_t n = 0; n < BLOCK; n++) {
            float wet = acc[n] * wet_gain, dry = (float)s[n];
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

/* Reverb object vtable slot 0x60042f94 (update method 0x60031e5d) */
void mt_fv_update(uint8_t *obj)
{
    uint32_t buf_l = *(volatile uint32_t *)(obj + RV_BUF_L), buf_r = *(volatile uint32_t *)(obj + RV_BUF_R);
#if defined(FV_PASSTHROUGH)
    (void)buf_l;
    (void)buf_r;
    ((update_fn)FW_REVERB_UPDATE)(obj);
#elif defined(FV_DIAG_CLEAR)
    if (FV->magic != FV_MAGIC && !obj[RV_BYPASS] && buf_l && buf_r) {
        fv_init(buf_l, buf_r);
        clear_step(BUFFER_SAMPLES / 2);
    }
    ((update_fn)FW_REVERB_UPDATE)(obj);
#elif defined(FV_DIAG_MUTED)
    if (obj[RV_BYPASS] || !buf_l || !buf_r)
        return;
    if (FV->magic != FV_MAGIC || FV->buf_l != buf_l || FV->buf_r != buf_r) {
        fv_init(buf_l, buf_r);
        FV->cleared = BUFFER_SAMPLES / 2;   /* not cleared: the output is muted */
    }
    freeverb_blocks(obj, 1);
#else
    if (FV->ready != FV_READY) {            /* still booting: the stock reverb */
        ((update_fn)FW_REVERB_UPDATE)(obj);
        return;
    }
    if (obj[RV_BYPASS] || !buf_l || !buf_r)
        return;
    if (FV->magic != FV_MAGIC || FV->buf_l != buf_l || FV->buf_r != buf_r)
        fv_init(buf_l, buf_r);
    if (FV->cleared < BUFFER_SAMPLES / 2) { /* clearing: silent, like the bypass */
        clear_step(CLEAR_WORDS);
        return;
    }
    freeverb_blocks(obj, 0);
#endif
}

/* Pattern screen vtable slot 0x600403dc (update method 0x600e1f99; chained): the unit
 * has booted, Freeverb may take over. */
void mt_fv_pattern_update(void *screen, void *a1, void *a2, void *a3)
{
    if (FV->ready != FV_READY) {
        FV->magic = 0;                      /* lay out and clear before the first block */
        FV->ready = FV_READY;
    }
    ((screen_update_fn)mt_fv_next_pattern_update)(screen, a1, a2, a3);
}

/* replaces the Config menu constructor's last "bl 0x601183e8" (Licenses) at 0x601193e6
 * (chained): a static constructor, so this runs at every start, before the audio. */
void *mt_fv_last_item(void *node, void *parent, const char *label, const char *help,
                      uint32_t type, const void *descriptor)
{
    FV->ready = 0;
    FV->magic = 0;
    return ((item_fn)mt_fv_next_last_item)(node, parent, label, help, type, descriptor);
}

/* ---- version text: Config shows 1.2.0-<FREEVERB_VERSION> --------------------------- */
const char mt_version_fmt[] = "%d.%d.%d-" FREEVERB_VERSION;
