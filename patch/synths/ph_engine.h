/*
 * The phase distortion engine for patch/synths (included by synths.c, after wt_engine.h).
 *
 * One engine voice per voice of a PHASE slot (a VAP voice object whose patch has PHASE's
 * model id), as wt_engine.h has for WAVE. It reads the slot's copy of the parameters
 * (synths.c keeps it from set_param) and renders 128 mono float samples per block into L.
 * PHASE's parameters sit at VAP's indices (synths.c's builder renames and re-ranges
 * VAP's), so the index map below is VAP's; docs/PD_SYNTH_PLAN.md section 4.3 lists them.
 *
 * Two DCOs (docs/PD_SYNTH_PLAN.md section 3; the formulas are this repository's own).
 * Each has a 32-bit phase p and plays a shape: Saw, Square, Pulse, Double Sine and
 * Saw-Pulse warp the phase, y = cos(2 pi W(p)), W piecewise linear and the identity at
 * DCW 0 (a pure sine); Reso I-III are a hard-synced cosine at r times the pitch under a
 * saw, triangle or trapezoid window, y = w(p) (1 - cos(2 pi r p)) - 1, r = 24^DCW. A fast
 * segment lasts at least PH_KNEE samples, r times the pitch stays below PH_R_CAP of the
 * rate, the Double Sine's depth and the Saw-Pulse's saw ease above about note 87 and 92:
 * that keeps the corners within the sample rate. A DCO can play two shapes on
 * alternate cycles (Wave 1+ / 2+, as the CZ: not two resonant ones). Each shape's mean is
 * worked out from its segments per block and taken off, ramped across the block, so
 * nothing carries DC and a note starts without a thump. The cosine is a 1,024-step table
 * (gen_phase_tables.py) with linear interpolation.
 *
 * Ring Mod puts DCO 1 x DCO 2 in DCO 2's place; Noise Mod jitters DCO 2's phase step by
 * noise (a square law: a roughness at 10 %, noise at 100 %, alike at every note). Three envelopes (the DCA, the DCW's with a depth per DCO, and the DCO's pitch
 * envelope, +-48 semitones), two LFOs and a 6-slot Mod Matrix, all per block, as WAVE's.
 * DCW Key Follow lowers (or raises) the DCW with the note around middle C, as the CZ's.
 */
#ifndef PH_ENGINE_H
#define PH_ENGINE_H

#include "ph_tables.h"

/* The parameters the engine reads (set_param indices: VAP's, then the own six) and their
 * place in a slot's parameter copy: every index up to 38 at its own place, Volume (39,
 * per voice) none, then 40-61 one place down. 61 places: all of the copy. */
#define PHASE_PARAMS        66u             /* VAP's 60, LFO 1's and LFO 2's Polarity, the effect's 4 */
enum {
    PP_WAVE1B, PP_WAVE2B, PP_WAVE1, PP_WAVE2, PP_MIX, PP_TUNE1, PP_TUNE2, PP_DETUNE, PP_FINE,
    PP_RESET1, PP_RESET2, PP_RING, PP_DCW1, PP_DCW2, PP_ENVAMT1,
    PP_PENV_ATT, PP_PENV_DEC, PP_PENV_SUS, PP_PENV_REL,     /* 15-18: the DCO (pitch) envelope */
    PP_DCA_ATT, PP_DCA_DEC, PP_DCA_SUS, PP_DCA_REL,         /* 19-22: the DCA (amp) envelope */
    PP_WENV_ATT, PP_WENV_DEC, PP_WENV_SUS, PP_WENV_REL,     /* 23-26: the DCW envelope */
    PP_LFO1, PP_LFO2 = PP_LFO1 + 4,                         /* 27-30, 31-34: Ratio, Waveform, Retrigger, Sync */
    PP_GLIDE_MODE = PP_LFO2 + 4, PP_GLIDE_TIME, PP_KEYFOLLOW, PP_ENVAMT2,   /* 35-38 */
    PP_NOISEMOD, PP_PENV_AMT,                               /* 40, 41 */
    PP_MTX,                                                 /* 42-59: 6 x Src, Dest, Amt */
    PP_LFO_POL = PP_MTX + 18,                               /* 60, 61 */
    PH_PARAMS = PP_LFO_POL + 2
};
_Static_assert((int)PH_PARAMS <= (int)WT_PARAMS, "PHASE's parameters fill the slot's copy");
static const uint8_t ph_param_slot[PHASE_PARAMS] = {
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19,
    20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38,
    WT_UNUSED, PP_NOISEMOD, PP_PENV_AMT,                                             /* 39-41 */
    PP_MTX, PP_MTX + 1, PP_MTX + 2, PP_MTX + 3, PP_MTX + 4, PP_MTX + 5,               /* 42-59 */
    PP_MTX + 6, PP_MTX + 7, PP_MTX + 8, PP_MTX + 9, PP_MTX + 10, PP_MTX + 11,
    PP_MTX + 12, PP_MTX + 13, PP_MTX + 14, PP_MTX + 15, PP_MTX + 16, PP_MTX + 17,
    PP_LFO_POL, PP_LFO_POL + 1,                                                       /* 60-61 */
    WT_UNUSED, WT_UNUSED, WT_UNUSED, WT_UNUSED,                                       /* 62-65: wt_fx.h */
};

#define PH_SHAPES           8u              /* Saw, Square, Pulse, Double Sine, Saw-Pulse, Reso I-III */
#define PH_RESO             5u              /* the first resonant shape */
/* The Mod Matrix's lists (synths.c's names) */
enum { PH_SRC_DCA, PH_SRC_DCW, PH_SRC_DCO, PH_SRC_LFO1, PH_SRC_LFO2, PH_SRC_VELOCITY, PH_SRC_KEY, PH_SRCS };
enum { PH_DST_PITCH, PH_DST_PITCH1, PH_DST_PITCH2, PH_DST_DCW, PH_DST_DCW1, PH_DST_DCW2, PH_DST_MIX,
       PH_DST_VOLUME, PH_DST_DETUNE, PH_DESTS };

/* One engine voice, tied to one VAP voice object (owner); its head as struct wt_voice's. */
struct ph_voice {
    uint32_t owner;                         /* the VAP voice object, 0 = free */
    uint16_t slot;
    uint8_t engine;                         /* ENGINE_PHASE */
    uint8_t pad0;
    float volume;                           /* Volume (39): velocity / 127 */
    float note;                             /* the note being played */
    float bend;                             /* semitones */
    struct wt_env aenv;                     /* the DCA envelope */
    uint8_t gate;                           /* a note is held */
    uint8_t started;                        /* the voice has played a note (for glide) */
    uint8_t sel[2];                         /* each DCO's pair: the shape playing this cycle (0, 1) */
    float pitch;                            /* where the glide is (a note number) */
    float glide_step;                       /* semitones per block towards `note` */
    struct wt_env wenv, penv;               /* the DCW and DCO (pitch) envelopes */
    uint32_t phase[2];
    uint32_t lfo_phase[2];                  /* the LFOs (Mod Matrix sources), per voice */
    float lfo_hold[2];                      /* Random S&H: the value held this cycle */
    uint8_t lfo_done[2];                    /* One Shot: the cycle has run */
    uint8_t fresh;                          /* a new note: the means start where they are, no ramp */
    uint8_t pad;
    uint32_t noise;                         /* noise generator (Noise Mod, Random S&H) */
    float mean[2];                          /* each DCO's mean last block (taken off, ramped) */
};

/* ---- the voice ---------------------------------------------------------------------- */

static void ph_voice_init(struct ph_voice *e, uint32_t slot)
{
    e->slot = (uint16_t)slot;
    e->volume = 1.0f;
    e->note = e->pitch = 60.0f;
    e->bend = 0.0f;
    e->glide_step = 0.0f;
    e->aenv.stage = e->wenv.stage = e->penv.stage = ENV_IDLE;
    e->aenv.level = e->wenv.level = e->penv.level = 0.0f;
    e->gate = 0;
    e->started = 0;
    e->fresh = 1;
    /* the DCOs start anywhere, each voice elsewhere (from its address), as WAVE's */
    e->noise = 0x9E3779B9u ^ (uint32_t)e;
    for (uint32_t k = 0; k < 2; k++) {
        e->noise = e->noise * 1664525u + 1013904223u;
        e->phase[k] = e->noise;
        e->sel[k] = 0;
        e->lfo_phase[k] = 0;
        e->lfo_hold[k] = 0.0f;
        e->lfo_done[k] = 0;
        e->mean[k] = 0.0f;
    }
}

static float ph_lfo_random(struct ph_voice *e)
{
    e->noise = e->noise * 1664525u + 1013904223u;
    return (float)(int32_t)e->noise * (1.0f / 2147483648.0f);
}

/* One block of LFO k, as WAVE's lfo_step: -1..1, or 0..1 when its Polarity is Unipolar */
static float ph_lfo_step(struct ph_voice *e, uint32_t k, const float *p, float tempo)
{
    const float *q = &p[PP_LFO1 + 4u * k];
    float hz;
    if (q[3] > 0.5f)
        hz = clampf(tempo, 20.0f, 999.0f) * (1.0f / 60.0f) / lfo_beats[list_pick(q[0], 16)];
    else
        hz = q[0];
    hz = clampf(hz, 0.0f, 100.0f);
    uint32_t mode = list_pick(q[2], 3), wave = list_pick(q[1], 6);
    uint32_t ph = e->lfo_phase[k];
    if (!(mode == LFO_ONE_SHOT && e->lfo_done[k])) {
        uint32_t next = ph + (uint32_t)(hz * (4294967296.0f * WT_BLOCK / WT_RATE));
        if (next < ph) {
            if (mode == LFO_ONE_SHOT) {
                e->lfo_done[k] = 1;
                next = 0xFFFFFFFFu;
            }
            e->lfo_hold[k] = ph_lfo_random(e);
        }
        e->lfo_phase[k] = ph = next;
    }
    float t = (float)(ph >> 8) * (1.0f / 16777216.0f), v;
    switch (wave) {
    case 0: v = 1.0f - 4.0f * (t < 0.5f ? 0.5f - t : t - 0.5f); break;  /* Triangle */
    case 1: v = sin_cycle(t); break;
    case 2: v = 2.0f * t - 1.0f; break;                                  /* Ramp Up */
    case 3: v = 1.0f - 2.0f * t; break;                                  /* Ramp Down */
    case 4: v = t < 0.5f ? 1.0f : -1.0f; break;                          /* Square */
    default: v = e->lfo_hold[k]; break;                                  /* Random S&H */
    }
    return p[PP_LFO_POL + k] > 0.5f ? 0.5f * (v + 1.0f) : v;
}

/* A note on, as WAVE's: Glide Mode 0 Always, 1 Overlap, 2 Legato, 3 Legato O. */
static void ph_note_on(struct ph_voice *e, const float *p, uint32_t note)
{
    float n = (float)note;
    float t = clampf(p[PP_GLIDE_TIME], 0.0f, 10.0f);
    uint32_t mode = (uint32_t)clampf(p[PP_GLIDE_MODE] + 0.5f, 0.0f, 3.0f);
    int overlap = e->gate;
    int glide = e->started && t > 0.0f && (mode == 0 || mode == 2 || overlap);
    int legato = overlap && mode >= 2;
    if (glide) {
        float d = n - e->pitch;
        e->glide_step = (d < 0 ? -d : d) / (t * (WT_RATE / WT_BLOCK));
    } else {
        e->pitch = n;
        e->glide_step = 0.0f;
    }
    e->note = n;
    for (uint32_t o = 0; o < 2; o++)
        if (p[PP_RESET1 + o] > 0.5f) {      /* Reset: from phase 0, the pair's first shape */
            e->phase[o] = 0;
            e->sel[o] = 0;
        }
    if (e->aenv.stage == ENV_IDLE)
        e->fresh = 1;
    if (!legato) {
        e->aenv.stage = ENV_ATTACK;
        e->wenv.stage = ENV_ATTACK;
        e->penv.stage = ENV_ATTACK;
        for (uint32_t k = 0; k < 2; k++)
            if (list_pick(p[PP_LFO1 + 4u * k + 2u], 3) != LFO_FREE) {
                e->lfo_phase[k] = 0;
                e->lfo_done[k] = 0;
                e->lfo_hold[k] = ph_lfo_random(e);
            }
    }
    e->gate = 1;
    e->started = 1;
}

static void ph_note_off(struct ph_voice *e)
{
    e->gate = 0;
    if (e->aenv.stage != ENV_IDLE)
        e->aenv.stage = ENV_RELEASE;
    if (e->wenv.stage != ENV_IDLE)
        e->wenv.stage = ENV_RELEASE;
    if (e->penv.stage != ENV_IDLE)
        e->penv.stage = ENV_RELEASE;
}

static void ph_voice_midi(struct ph_voice *e, const uint8_t *msg, const float *p)
{
    uint32_t type = msg[0];
    if (type == MSG_NOTE_ON && msg[3]) {
        ph_note_on(e, p, msg[2]);
    } else if (type == MSG_NOTE_ON || type == MSG_NOTE_OFF) {
        if ((float)msg[2] == e->note && e->gate)
            ph_note_off(e);
    } else if (type == MSG_BEND) {
        int16_t v = (int16_t)(msg[2] | msg[3] << 8);
        e->bend = (float)v * (2.0f * 12.0f * BEND_OCTAVES);
    }
}

static int ph_voice_sounding(const struct ph_voice *e)
{
    return e->aenv.stage != ENV_IDLE;
}

/* ---- the shapes ----------------------------------------------------------------------- */

#define PH_KNEE             4.0f            /* a fast segment lasts at least this many samples */
#define PH_R_MAX            24.0f           /* the resonance at DCW 100 % (r = 24^DCW) */
#define PH_LOG2_R_MAX       4.5849625f
#define PH_R_CAP            0.25f           /* r x the pitch stays below this x the rate */
#define PH_NO_X             2.0f            /* an unused breakpoint (past the cycle's end) */
#define PH_DS_EASE          0.0085f         /* Double Sine: a <= this / kmin^2 (a cycle under 43 samples) */

/* One shape at one DCW and pitch: a piecewise-linear map of up to 4 segments, W = b + m p
 * on [x_i, x_i+1) (x0 = 0). Warp shapes: y = cos(2 pi W). Resonant ones: W is the window,
 * y = W (1 - cos(2 pi r p)) - 1. `mean`: y's mean over a cycle. */
struct ph_shape { float x1, x2, x3; float b[4], m[4]; float r, mean; uint32_t reso; };

/* cos(2 pi w), w >= 0 (wrapped) */
static inline float ph_cos_cycle(float w)
{
    float t = w * (float)PH_COS_SIZE;
    uint32_t i = (uint32_t)t;
    float fr = t - (float)i;
    i &= PH_COS_SIZE - 1u;
    float a = ph_cos[i];
    return a + (ph_cos[i + 1] - a) * fr;
}

/* cos and sin of 2 pi x for any x (the means' integrals) */
static float ph_cos_any(float x)
{
    float f = x - (float)(int32_t)x;
    return ph_cos_cycle(f < 0.0f ? f + 1.0f : f);
}

static inline float ph_sin_any(float x)
{
    return ph_cos_any(x - 0.25f);
}

static void seg_set(struct ph_shape *s, uint32_t i, float x, float w0, float m)
{
    s->b[i] = w0 - m * x;
    s->m[i] = m;
}

/* The mean of y over [xa, xb) on segment i */
static float seg_mean(const struct ph_shape *s, uint32_t i, float xa, float xb)
{
    float b = s->b[i], m = s->m[i];
    if (!(xb > xa))
        return 0.0f;
    if (!s->reso) {                         /* the integral of cos(2 pi (b + m p)) */
        if (m < 1e-3f && m > -1e-3f)
            return ph_cos_any(b + m * 0.5f * (xa + xb)) * (xb - xa);
        return (ph_sin_any(b + m * xb) - ph_sin_any(b + m * xa)) * (1.0f / (2.0f * 3.14159265f)) / m;
    }
    /* (b + m p) (1 - cos(w p)) - 1, w = 2 pi r: the window's integral less
     * [(b + m p) sin(w p) / w + m cos(w p) / w^2] */
    float r = s->r, w = 2.0f * 3.14159265f * r;
    float win = b * (xb - xa) + 0.5f * m * (xb * xb - xa * xa);
    float hi = (b + m * xb) * ph_sin_any(r * xb) / w + m * ph_cos_any(r * xb) / (w * w);
    float lo = (b + m * xa) * ph_sin_any(r * xa) / w + m * ph_cos_any(r * xa) / (w * w);
    return win - (hi - lo) - (xb - xa);
}

/* shape (0-7) at DCW d (0..1) for a pitch of hz */
static void shape_setup(struct ph_shape *s, uint32_t shape, float d, float hz)
{
    float kmin = clampf(PH_KNEE * hz * (1.0f / WT_RATE), 0.0f, 0.5f);
    float k, e;
    s->x1 = s->x2 = s->x3 = PH_NO_X;
    s->reso = shape >= PH_RESO;
    s->r = 1.0f;
    switch (shape) {
    case 0:                                 /* Saw: 0 -> 1/2 over [0, k], 1/2 -> 1 over [k, 1] */
        k = clampf(0.5f * (1.0f - d), kmin, 0.5f);
        seg_set(s, 0, 0.0f, 0.0f, 0.5f / k);
        s->x1 = k; seg_set(s, 1, k, 0.5f, 0.5f / (1.0f - k));
        break;
    case 1:                                 /* Square: a rise, a hold, a rise, a hold */
        k = clampf(0.5f * (1.0f - d), kmin, 0.5f);
        seg_set(s, 0, 0.0f, 0.0f, 0.5f / k);
        s->x1 = k; seg_set(s, 1, k, 0.5f, 0.0f);
        s->x2 = 0.5f; seg_set(s, 2, 0.5f, 0.5f, 0.5f / k);
        s->x3 = 0.5f + k; seg_set(s, 3, 0.5f + k, 1.0f, 0.0f);
        break;
    case 2:                                 /* Pulse: the cycle in [0, k], then hold; k >= 10 % */
        k = clampf(1.0f - 0.9f * d, 2.0f * kmin, 1.0f);
        seg_set(s, 0, 0.0f, 0.0f, 1.0f / k);
        s->x1 = k; seg_set(s, 1, k, 1.0f, 0.0f);
        break;
    case 3: {                               /* Double Sine: two segments of slope 1 + a */
        float a = 0.6f * d;
        if (a * kmin * kmin > PH_DS_EASE)   /* the corner at 1/2 grows with a: eased at high notes */
            a = PH_DS_EASE / (kmin * kmin);
        seg_set(s, 0, 0.0f, 0.0f, 1.0f + a);
        s->x1 = 0.5f; seg_set(s, 1, 0.5f, 0.5f - 0.5f * a, 1.0f + a);
        break;
    }
    case 4:                                 /* Saw-Pulse: a saw over [0, e], then hold; e >= 3 knees */
        e = clampf(1.0f - 0.5f * d, 3.0f * kmin < 1.0f ? 3.0f * kmin : 1.0f, 1.0f);
        k = clampf(0.5f * e * (1.0f - d), kmin < 0.5f * e ? kmin : 0.5f * e, 0.5f * e);
        seg_set(s, 0, 0.0f, 0.0f, 0.5f / k);
        s->x1 = k; seg_set(s, 1, k, 0.5f, 0.5f / (e - k));
        s->x2 = e; seg_set(s, 2, e, 1.0f, 0.0f);
        break;
    default:                                /* Reso I-III: the window; r = 24^d, capped */
        s->r = clampf(exp2f_fast(PH_LOG2_R_MAX * d), 1.0f,
                      clampf(PH_R_CAP * WT_RATE / hz, 1.0f, PH_R_MAX));
        if (shape == 5) {                   /* saw window: 1 - p */
            seg_set(s, 0, 0.0f, 1.0f, -1.0f);
        } else if (shape == 6) {            /* triangle: 1 - |2p - 1| */
            seg_set(s, 0, 0.0f, 0.0f, 2.0f);
            s->x1 = 0.5f; seg_set(s, 1, 0.5f, 1.0f, -2.0f);
        } else {                            /* trapezoid: min(1, 2 (1 - p)) */
            seg_set(s, 0, 0.0f, 1.0f, 0.0f);
            s->x1 = 0.5f; seg_set(s, 1, 0.5f, 1.0f, -2.0f);
        }
        break;
    }
    float x[5] = {0.0f, s->x1, s->x2, s->x3, 1.0f}, mean = 0.0f;
    for (uint32_t i = 0; i < 4; i++)
        mean += seg_mean(s, i, x[i] < 1.0f ? x[i] : 1.0f, x[i + 1] < 1.0f ? x[i + 1] : 1.0f);
    s->mean = mean;
}

static inline float seg_eval(const struct ph_shape *s, float p)
{
    uint32_t i = (p >= s->x1) + (p >= s->x2) + (p >= s->x3);
    return s->b[i] + s->m[i] * p;
}

#define KIND_WARP           0
#define KIND_RESO           1
#define KIND_PAIR           2               /* either, per cycle (the pair's two shapes) */

/* One DCO's block into out (or added to it, scaled by g): `sel` the pair's shape now,
 * `nmod` the Noise Mod's jitter (DCO 2), the phase returned */
static inline __attribute__((always_inline)) uint32_t
dco_loop(uint32_t ph, uint32_t inc, uint8_t *sel, const struct ph_shape *sh, const int kind, float *out,
         float g, const int add, uint32_t *nzp, int32_t nmod, const int noise)
{
    uint32_t nz = *nzp;
    const struct ph_shape *s = &sh[*sel];
    for (uint32_t n = 0; n < WT_BLOCK; n++) {
        uint32_t step = inc;
        if (noise) {
            nz = nz * 1664525u + 1013904223u;
            step += (uint32_t)(((int32_t)nz >> 16) * nmod);
        }
        ph += step;
        if (kind == KIND_PAIR && ph < step) {   /* a new cycle: the other shape */
            *sel ^= 1u;
            s = &sh[*sel];
        }
        float p = (float)(ph >> 8) * (1.0f / 16777216.0f);
        float w = seg_eval(s, p), y;
        if (kind == KIND_PAIR ? s->reso : kind == KIND_RESO) {
            float x = s->r * p;
            y = w * (1.0f - ph_cos_cycle(x - (float)(uint32_t)x)) - 1.0f;
        } else {
            y = ph_cos_cycle(w);
        }
        out[n] = add ? out[n] + y * g : y * g;
    }
    *nzp = nz;
    return ph;
}

static uint32_t dco_run(uint32_t ph, uint32_t inc, uint8_t *sel, const struct ph_shape *sh, uint32_t two,
                        float *out, float g, int add, uint32_t *nz, int32_t nmod)
{
    if (nmod)
        return add ? dco_loop(ph, inc, sel, sh, KIND_PAIR, out, g, 1, nz, nmod, 1)
                   : dco_loop(ph, inc, sel, sh, KIND_PAIR, out, g, 0, nz, nmod, 1);
    if (two)
        return add ? dco_loop(ph, inc, sel, sh, KIND_PAIR, out, g, 1, nz, 0, 0)
                   : dco_loop(ph, inc, sel, sh, KIND_PAIR, out, g, 0, nz, 0, 0);
    if (sh[*sel].reso)
        return add ? dco_loop(ph, inc, sel, sh, KIND_RESO, out, g, 1, nz, 0, 0)
                   : dco_loop(ph, inc, sel, sh, KIND_RESO, out, g, 0, nz, 0, 0);
    return add ? dco_loop(ph, inc, sel, sh, KIND_WARP, out, g, 1, nz, 0, 0)
               : dco_loop(ph, inc, sel, sh, KIND_WARP, out, g, 0, nz, 0, 0);
}

/* ---- the voice's block ----------------------------------------------------------------- */

#define PH_LEVEL_GAIN       0.25f           /* the output level: an Init note about WAVE's; a 4-note chord
                                             * at full velocity can't reach the slot's clip */
#define PH_KEY_SPAN         48.0f           /* Key Follow 100 %: DCW -100 % four octaves above middle C */
#define PH_DETUNE_CENTS     100.0f          /* the Mod Matrix's Detune: 100 % is 100 cents more on DCO 2 */
#define PH_NOISE_WIDTH      1.0f            /* Noise Mod 100 %: the pitch spread about 2 x the pitch (noise) */

static inline float ph_sqrt(float x)
{
    float r;
    __asm__("vsqrt.f32 %0, %1" : "=t"(r) : "t"(x));
    return r;
}

/* The Mod Matrix: 6 slots of Src, Dest, Amt (-4..4, shown as +-400 %), per block */
static void ph_matrix(float *m, const float *src, const float *p)
{
    for (uint32_t d = 0; d < PH_DESTS; d++)
        m[d] = 0.0f;
    for (uint32_t s = 0; s < MOD_SLOTS; s++) {
        const float *q = &p[PP_MTX + 3u * s];
        float amt = clampf(q[2], -4.0f, 4.0f);
        if (amt != 0.0f)
            m[list_pick(q[1], PH_DESTS)] += amt * src[list_pick(q[0], PH_SRCS)];
    }
}

/* `tempo`: the render context's BPM (for synced LFOs) */
static void ph_voice_render(struct ph_voice *e, const float *p, float *out, float tempo)
{
    float a0 = e->aenv.level;
    if (e->aenv.stage == ENV_IDLE) {        /* idle: silence; Free Running LFOs run on */
        for (uint32_t k = 0; k < 2; k++)
            if (list_pick(p[PP_LFO1 + 4u * k + 2u], 3) == LFO_FREE)
                (void)ph_lfo_step(e, k, p, tempo);
        for (uint32_t n = 0; n < WT_BLOCK; n += 4) {
            out[n] = 0.0f;
            out[n + 1] = 0.0f;
            out[n + 2] = 0.0f;
            out[n + 3] = 0.0f;
        }
        return;
    }
    float a1 = env_step(&e->aenv, &p[PP_DCA_ATT]);
    float we = env_step(&e->wenv, &p[PP_WENV_ATT]);
    float pe = env_step(&e->penv, &p[PP_PENV_ATT]);
    float src[PH_SRCS], m[PH_DESTS];
    src[PH_SRC_DCA] = a1;
    src[PH_SRC_DCW] = we;
    src[PH_SRC_DCO] = pe;
    src[PH_SRC_LFO1] = ph_lfo_step(e, 0, p, tempo);
    src[PH_SRC_LFO2] = ph_lfo_step(e, 1, p, tempo);
    src[PH_SRC_VELOCITY] = clampf(e->volume, 0.0f, 1.0f);
    src[PH_SRC_KEY] = clampf((e->pitch - 60.0f) * (1.0f / 60.0f), -2.0f, 2.0f);
    ph_matrix(m, src, p);
    if (e->glide_step > 0.0f) {
        float d = e->note - e->pitch;
        if (d > e->glide_step)
            e->pitch += e->glide_step;
        else if (d < -e->glide_step)
            e->pitch -= e->glide_step;
        else
            e->pitch = e->note;
    } else {
        e->pitch = e->note;
    }
    /* each DCO's pitch and DCW, and its shapes */
    float base = e->pitch + clampf(p[PP_FINE], -100.0f, 100.0f) * 0.01f + clampf(e->bend, -48.0f, 48.0f)
                 + clampf(p[PP_PENV_AMT], -48.0f, 48.0f) * pe;
    float kf = clampf(p[PP_KEYFOLLOW], -1.0f, 1.0f) * (e->pitch - 60.0f) * (1.0f / PH_KEY_SPAN);
    struct ph_shape sh[2][2];
    uint32_t inc[2], two[2];
    for (uint32_t k = 0; k < 2; k++) {
        float note = base + clampf(p[PP_TUNE1 + k], -36.0f, 36.0f)
                     + clampf((m[PH_DST_PITCH] + m[PH_DST_PITCH1 + k]) * MOD_SEMITONES, -96.0f, 96.0f);
        if (k)
            note += clampf(clampf(p[PP_DETUNE], 0.0f, 100.0f) + m[PH_DST_DETUNE] * PH_DETUNE_CENTS,
                           -400.0f, 400.0f) * 0.01f;
        float hz = clampf(note_hz(note), 1.0f, 20000.0f);
        inc[k] = (uint32_t)(hz * (4294967296.0f / WT_RATE));
        float d = clampf(clampf(p[PP_DCW1 + k], 0.0f, 1.0f)
                         + clampf(p[k ? PP_ENVAMT2 : PP_ENVAMT1], -1.0f, 1.0f) * we
                         + m[PH_DST_DCW] + m[PH_DST_DCW1 + k] - kf, 0.0f, 1.0f);
        uint32_t a = list_pick(p[PP_WAVE1 + k], PH_SHAPES);
        uint32_t b = list_pick(p[PP_WAVE1B + k], 1u + PH_SHAPES);    /* 0: Off */
        two[k] = b && !(a >= PH_RESO && b - 1u >= PH_RESO);           /* not two resonant ones */
        if (!two[k])
            e->sel[k] = 0;
        shape_setup(&sh[k][0], a, d, hz);
        if (two[k])
            shape_setup(&sh[k][1], b - 1u, d, hz);
    }
    float mean[2];
    for (uint32_t k = 0; k < 2; k++) {
        mean[k] = two[k] ? 0.5f * (sh[k][0].mean + sh[k][1].mean) : sh[k][0].mean;
        if (e->fresh)
            e->mean[k] = mean[k];
    }
    float mix = (clampf(clampf(p[PP_MIX], -1.0f, 1.0f) + m[PH_DST_MIX], -1.0f, 1.0f) + 1.0f) * 0.5f;
    float vol = clampf(e->volume, 0.0f, 2.0f) * PH_LEVEL_GAIN * clampf(1.0f + m[PH_DST_VOLUME], 0.0f, 4.0f);
    float g1 = (1.0f - mix) * vol, g2 = mix * vol;
    /* Noise Mod: DCO 2's step jittered by +-Noise x PH_NOISE_WIDTH x sqrt(step) (in cycles),
     * so its phase wanders by the same share of the pitch at every note; the jitter is
     * the noise's top 16 bits x nmod */
    int32_t nmod = (int32_t)clampf(clampf(p[PP_NOISEMOD], 0.0f, 1.0f) * (2.0f * PH_NOISE_WIDTH)
                                   * ph_sqrt((float)inc[1]), 0.0f, 65535.0f);
    if (nmod && !two[1])                    /* the noise's loop plays a pair: both halves one shape */
        sh[1][1] = sh[1][0];
    float m1 = e->mean[0], dm1 = (mean[0] - m1) * (1.0f / WT_BLOCK);
    float m2 = e->mean[1], dm2 = (mean[1] - m2) * (1.0f / WT_BLOCK);
    float amp = a0, damp = (a1 - a0) * (1.0f / WT_BLOCK);
    if (p[PP_RING] > 0.5f) {                /* Ring Mod: DCO 1 x DCO 2 in DCO 2's place */
        float t2[WT_BLOCK];
        e->phase[0] = dco_run(e->phase[0], inc[0], &e->sel[0], sh[0], two[0], out, 1.0f, 0, &e->noise, 0);
        e->phase[1] = dco_run(e->phase[1], inc[1], &e->sel[1], sh[1], two[1], t2, 1.0f, 0, &e->noise, nmod);
        for (uint32_t n = 0; n < WT_BLOCK; n++) {
            float y1 = out[n] - m1, y2 = t2[n] - m2;
            out[n] = limit(y1 * (g1 + g2 * y2) * amp);
            m1 += dm1;
            m2 += dm2;
            amp += damp;
        }
    } else {
        e->phase[0] = dco_run(e->phase[0], inc[0], &e->sel[0], sh[0], two[0], out, g1, 0, &e->noise, 0);
        e->phase[1] = dco_run(e->phase[1], inc[1], &e->sel[1], sh[1], two[1], out, g2, 1, &e->noise, nmod);
        float dc = g1 * m1 + g2 * m2, ddc = g1 * dm1 + g2 * dm2;
        for (uint32_t n = 0; n < WT_BLOCK; n++) {
            out[n] = limit((out[n] - dc) * amp);
            dc += ddc;
            amp += damp;
        }
    }
    e->mean[0] = mean[0];
    e->mean[1] = mean[1];
    e->fresh = 0;
}

#endif
