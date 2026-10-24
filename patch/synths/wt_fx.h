/*
 * WAVE's effect (included by synths.c after wt_engine.h): one per slot, on the slot's
 * summed output, after every voice has rendered (voice 0's +0x14, docs/FINDINGS.md
 * section 23). The slot plays in stereo (its patch's channel count is 2), so the effect
 * writes both sides; with Effect None, or before it has faded in, R is a copy of L.
 *
 * Effect (62): None or Chorus. Rate (63), Depth (64) and Mix (65) are the effect's.
 *
 * Chorus, after the Roland Juno's: one delay line, read by two taps that a triangle LFO
 * moves in opposite directions, one for each side, around 3.5 ms; Depth 100 % swings
 * them +-1.85 ms (1.65-5.35 ms, the Juno's Chorus II range), 50 % +-0.93 ms (about its
 * Chorus I). Rate is 0.05-5 Hz (0.5 Hz at 50 %). Each tap is read with linear
 * interpolation and goes through a one-pole low-pass at 9 kHz, as the Juno's
 * bucket-brigade delay dulls its copy. Mix crossfades from the dry signal (0 %) to the
 * taps alone (100 %); at 50 % the two are equal, as on the Juno. The crossfade keeps the
 * power of a broad sound: dry and taps are each scaled by 1 / sqrt((1 - Mix)^2 + Mix^2),
 * so at 50 % each is at 0.71 and the chorus is as loud as the dry sound.
 *
 * Changes are smooth: Mix and Depth move at most FX_STEP per block, so switching the
 * effect on or off fades it in or out over about 60 ms. The delay line starts empty each
 * time the effect starts.
 *
 * The state lives in the patch's late RAM block (tools/patch_ptf.py "late_ram": maybe in
 * SDRAM, so only used once the unit has booted). It holds garbage at power-on: the first
 * block after a start clears it and copies the effect's values from each slot's patch;
 * after that set_param keeps them, as it keeps the engine's.
 */
#ifndef WT_FX_H
#define WT_FX_H

#define FX_FIRST            62u             /* the first effect parameter (after the Polarity switches) */
#define FX_PARAMS           4u
enum { FX_EFFECT, FX_RATE, FX_DEPTH, FX_MIX };
enum { FX_NONE, FX_CHORUS, FX_TYPES };
#define FX_LEN              512u            /* the delay line: 11.6 ms (a power of two) */
#define FX_STEP             0.05f           /* Mix and Depth: the most they move per block */
#define CHORUS_CENTRE       (3.5e-3f * WT_RATE)
#define CHORUS_SWING        (1.85e-3f * WT_RATE)    /* at Depth 100 % */
#define CHORUS_LP           0.7226f         /* 1 - exp(-2 pi 9 kHz / 44.1 kHz) */
#define RATE_MIN            0.05f           /* Rate 0: 0.05 Hz; 1: 5 Hz (x 100) */
#define RATE_OCTAVES        6.6438562f      /* log2(100) */

/* The effect's defaults: the Init patch's values, and an older patch's (saved without them) */
static const float fx_defaults[FX_PARAMS] = {(float)FX_NONE, 0.5f, 0.5f, 0.5f};

struct wt_fx {
    float buf[FX_LEN];
    float p[FX_PARAMS];                     /* the slot's effect values (set_param's copy) */
    uint32_t voice;                         /* the slot's voice 0 this ran for (another: start over) */
    uint32_t running;                       /* the delay line is in use */
    uint32_t pos;                           /* where the next sample goes */
    float phase;                            /* the LFO, 0..1 */
    float lp[2];                            /* the taps' low-passes */
    float mix, depth;                       /* where the last block ended */
};

static inline float fx_sqrt(float x)
{
    float r;
    __asm__("vsqrt.f32 %0, %1" : "=t"(r) : "t"(x));
    return r;
}

/* Mix m -> the dry and wet gains */
static void fx_gains(float m, float *dry, float *wet)
{
    float k = 1.0f / fx_sqrt((1.0f - m) * (1.0f - m) + m * m);
    *dry = (1.0f - m) * k;
    *wet = m * k;
}

static float rate_hz(float v)
{
    return RATE_MIN * exp2f_fast(clampf(v, 0.0f, 1.0f) * RATE_OCTAVES);
}

static void fx_start(struct wt_fx *s)
{
    for (uint32_t n = 0; n < FX_LEN; n++)
        s->buf[n] = 0.0f;
    s->pos = 0;
    s->lp[0] = s->lp[1] = 0.0f;
    s->mix = 0.0f;
    s->depth = clampf(s->p[FX_DEPTH], 0.0f, 1.0f);
    s->running = 1;
}

static inline float step_to(float from, float to)
{
    return to > from + FX_STEP ? from + FX_STEP : to < from - FX_STEP ? from - FX_STEP : to;
}

static inline float tap(const float *buf, uint32_t pos, float d)
{
    float at = (float)(pos + FX_LEN) - d;   /* d is 1..FX_LEN - 2 samples back */
    uint32_t i = (uint32_t)at;
    float f = at - (float)i;
    float a = buf[i & (FX_LEN - 1u)], b = buf[(i + 1u) & (FX_LEN - 1u)];
    return a + f * (b - a);
}

/* One block of a slot's output: L in (mono), L and R out */
static void wt_fx_run(struct wt_fx *s, float *L, float *R)
{
    uint32_t type = list_pick(s->p[FX_EFFECT], FX_TYPES);
    float want = type == FX_CHORUS ? clampf(s->p[FX_MIX], 0.0f, 1.0f) : 0.0f;
    if (!s->running && type == FX_NONE) {
        for (uint32_t n = 0; n < WT_BLOCK; n++)
            R[n] = L[n];
        return;
    }
    if (!s->running)
        fx_start(s);
    float m0 = s->mix, m1 = step_to(m0, want);
    float d0 = s->depth, d1 = step_to(d0, clampf(s->p[FX_DEPTH], 0.0f, 1.0f));
    float g0, w0, g1, w1;
    fx_gains(m0, &g0, &w0);
    fx_gains(m1, &g1, &w1);
    float dg = (g1 - g0) * (1.0f / WT_BLOCK), dw = (w1 - w0) * (1.0f / WT_BLOCK);
    float dd = (d1 - d0) * (CHORUS_SWING / WT_BLOCK);
    float gd = g0, gw = w0, swing = d0 * CHORUS_SWING;
    float inc = rate_hz(s->p[FX_RATE]) * (1.0f / WT_RATE), ph = s->phase;
    float lpl = s->lp[0], lpr = s->lp[1];
    uint32_t pos = s->pos;
    for (uint32_t n = 0; n < WT_BLOCK; n++) {
        float x = L[n];
        s->buf[pos] = x;
        float tri = ph < 0.5f ? 4.0f * ph - 1.0f : 3.0f - 4.0f * ph;
        ph += inc;
        if (ph >= 1.0f)
            ph -= 1.0f;
        float d = swing * tri;
        lpl += CHORUS_LP * (tap(s->buf, pos, CHORUS_CENTRE + d) - lpl);
        lpr += CHORUS_LP * (tap(s->buf, pos, CHORUS_CENTRE - d) - lpr);
        L[n] = gd * x + gw * lpl;
        R[n] = gd * x + gw * lpr;
        pos = (pos + 1u) & (FX_LEN - 1u);
        gd += dg;
        gw += dw;
        swing += dd;
    }
    s->pos = pos;
    s->phase = ph;
    s->lp[0] = lpl;
    s->lp[1] = lpr;
    s->mix = m1;
    s->depth = d1;
    if (type == FX_NONE && m1 <= 0.0f)     /* faded out */
        s->running = 0;
}

#endif
