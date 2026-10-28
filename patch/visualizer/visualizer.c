/*
 * visualizer: a pad visualizer (Polyend Tracker+ firmware 1.2.0).
 *
 * Hold Note, Instrument, FX1 and FX2 together while a pattern plays (Pattern screen,
 * record off, not performance mode): the 48 pads show slow drifting "smoke" with
 * ripples spreading from every note that plays. Press any of the four again to go back
 * to the stock pads. The selected column stays as it was before the four were pressed.
 *
 * The pieces:
 *  - Audio side (the sequencer's interrupt), all of the animation's state:
 *    mt_vz_seq_advance on the bl seq_advance in app_audio_block (0x600aa7f8, chained)
 *    keeps the take's clock (seconds since playback started, from the seconds each
 *    audio block covers) and eases the smoke towards its targets; a new take resets
 *    everything. mt_vz_advance_step, on the word the sequencer's ITCM code jumps
 *    through to seq_advance_step (flash 0x601acf00, chained), reads the step a track
 *    has just moved to and turns its note, instrument and FX into a ripple and a nudge
 *    of the smoke. So the animation follows the music and the audio clock only: a
 *    fresh play of a pattern looks the same every time, and a later pass of a loop
 *    (or the next pattern in song mode) differs, as the smoke keeps the history.
 *    While the visualizer is hidden only the take's clock advances: the ripples and
 *    the smoke (a pattern read per track step) cost the audio interrupt nothing then.
 *  - The pads: the pad LED object's one virtual method, setPad (0x600a1bf1, the word
 *    at 0x60038298, chained), is replaced by mt_vz_set_pad. While the visualizer shows,
 *    the firmware's own pad writes are dropped (its pad manager at 0x200171d0 still
 *    keeps its layers, so the stock pads come back exactly when it stops); the
 *    visualizer writes through the original method.
 *  - Drawing: mt_vz_pattern_update, on the Pattern screen's update method (vtable word
 *    0x600403dc, chained), draws a frame every 30 ms while it shows, and hides it when
 *    playback stops, record goes on or performance mode starts.
 *  - Sending the frame: the pad LED driver's flush (its one vtable word, 0x600373b8 =
 *    0x60092931, chained) runs from the main loop whenever a pad changed, and it blocks
 *    the main loop while it talks to the IS31FL3731 over I2C: it reads back and checks
 *    13 of the chip's control registers, one transaction each, then sends all 180 bytes
 *    of enables, blink bits and brightness. Fine for the stock pads, which change now
 *    and then; at a frame every few tens of ms it starves the screen. So while the
 *    visualizer shows, mt_vz_flush sends only the brightness bytes that changed, in one
 *    burst (every pad stays enabled; dark is brightness 0). The first flush after it
 *    appears, and every flush once it's hidden, is the stock one.
 *  - The buttons: the Pattern screen's Note, Instrument, FX1 and FX2 callbacks (flash
 *    literals 0x600df2dc..0x600df2e8, used only when that screen registers them) are
 *    wrapped. With two or more of the four held while it could show, the screen's
 *    "held" flags are cleared, so the long-press action (after 180 ms) doesn't fire;
 *    the fourth press switches it on (and is not passed on), then the column selected
 *    before the first press is selected again. While it shows, a press of any of the
 *    four switches it off and is not passed on, nor are the other presses until all
 *    four are up again.
 *  - Stepping aside: while a bottom-row button shows its own pads (holding Pattern
 *    shows the patterns; the screen's pad mode at +0x1354 is 0 or more, -1 normally),
 *    the stock pads show, as with record on.
 *
 * Shared hooks go through chain words (mt_vz_next_*) that tools/patch_ptf.py fills with
 * what the site pointed to before, so other patches can hook them too.
 */
#include <stdint.h>

#ifndef VIS_VERSION
#define VIS_VERSION "vz6"
#endif

/* ---- firmware addresses (1.2.0) -------------------------------------------------- */

#define FW_SEQ              ((uint8_t *)0x20017274u)
#define SEQ_PATTERN         0x14u           /* pointer: the playing pattern */
#define SEQ_PERFORMANCE     0x1Du
#define SEQ_PLAYING         0x2Au
#define SEQ_TRACK(t)        (0x68u * (t))
#define TRK_STEP            0x88u           /* uint16: current step */
#define TRACKS              16u

#define PAT_TRACK(t)        (0x1Cu + 0x301u * (t))  /* byte: last step, then 128 steps */
#define STEP_SIZE           6u              /* note, instrument, FX1 type, value, FX2 type, value */
#define NOTE_EMPTY          0xFFu
#define FX_VELOCITY         18u             /* V */

#define FW_MUTE             ((volatile uint8_t *)0x202046E0u)  /* + track: muted */
#define FW_TEMPO            ((volatile float *)0x20204774u)
#define FW_SETTINGS         ((volatile uint8_t *)0x2002061Cu)
#define SET_PADS_DIM        0x34u           /* pad brightness levels, 0..31 */
#define SET_PADS_BRIGHT     0x35u
#define FW_TIMER            ((void *volatile *)0x20027D18u)    /* timer object: vtable[0] = ms */

#define FW_PAD_MGR          ((volatile uint8_t *)0x200171D0u)  /* app pad manager */
#define MGR_LAYER_A         0x04u           /* 48 levels */
#define MGR_LAYER_B         0x34u           /* 48 levels */
#define MGR_LEDS            0x98u           /* pointer: the pad LED object */
#define PADS                48u
#define PAD_COLS            12u
#define PAD_ROWS            4u
#define LEVEL_MAX           31u

#define FW_PATTERN_FIELDS   ((volatile uint8_t *)0x20022770u)  /* Pattern screen + 0x1000 */
#define PS_RECORD           0x254u
#define PS_COLUMN           0x255u          /* 0 Note, 1 Instrument, 2 FX1, 3 FX2 */
#define PS_HELD             0x4C7u          /* 4 bytes: column button held (long press) */
#define PS_PAD_MODE         0x354u          /* int8: -1 normal; 0 patterns (Pattern held), 1, 2 */

/* The IS31FL3731 driver object (pad LED object + 4) */
#define DRV_BUS             0x04u           /* pointer: the I2C bus object (vtable +0xc: write) */
#define DRV_PWM             0x08u           /* 144 brightness bytes, slot = pad + pad / 12 * 4 */
#define DRV_ADDR            0x98u           /* byte: I2C address (0x74) */
#define DRV_PAGE            0x99u           /* byte: the frame page the PWM bytes go to */
#define DRV_DIRTY           0x14Du          /* byte: a pad changed since the last flush */
#define CHIP_PAGE_REG       0xFDu
#define CHIP_PWM_REG        0x24u
#define BUS_WRITE           0x0Cu           /* bus vtable offset */

typedef void (*advance_fn)(void *seq, float seconds);
typedef uint32_t (*advance_step_fn)(void *seq, uint32_t track);
typedef void (*update_fn)(void *screen, void *a1, void *a2, void *a3);
typedef void (*set_pad_fn)(void *leds, uint32_t pad, uint32_t on, uint32_t level);
typedef void (*button_fn)(uint32_t state);
typedef uint32_t (*ms_fn)(void *timer);
typedef uint32_t (*flush_fn)(void *driver);
typedef uint32_t (*i2c_write_fn)(void *bus, uint32_t addr, uint32_t reg, uint32_t reg_size,
                                 const void *buf, uint32_t len);     /* 0 done, else failed */

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_vz_next_seq_advance);         /* seq_advance (veneer 0x600aa859) */
CHAIN_WORD(mt_vz_next_advance_step);        /* seq_advance_step 0x600ad011 */
CHAIN_WORD(mt_vz_next_set_pad);             /* pad LED setPad 0x600a1bf1 */
CHAIN_WORD(mt_vz_next_pattern_update);      /* Pattern screen update 0x600e1f99 */
CHAIN_WORD(mt_vz_next_note);                /* Note button 0x600d20ad */
CHAIN_WORD(mt_vz_next_instrument);          /* Instrument button 0x600d4495 */
CHAIN_WORD(mt_vz_next_fx1);                 /* FX1 button 0x600e308d */
CHAIN_WORD(mt_vz_next_fx2);                 /* FX2 button 0x600e2fad */
CHAIN_WORD(mt_vz_next_flush);               /* pad LED driver flush 0x60092931 */

/* ---- tuning ---------------------------------------------------------------------- */

#define FRAME_MS            30u             /* a frame every 30 ms while it shows */
#define SCREEN_GONE_MS      200u            /* no Pattern screen update for this long: elsewhere */
#define BUTTONS_STALE_MS    1500u           /* no column button event for this long: forget them */
#define RIPPLES             12u
#define SMOKE_LEVEL         0.35f           /* the smoke's share of full brightness */
#define SMOKE_EASE          0.35f           /* per second: how fast the smoke follows a nudge */
#define RIPPLE_GAP_BEATS    0.5f            /* at least half a beat between new ripples */
#define RIPPLE_RISE         0.5f            /* seconds a ripple takes to fade in */

/* ---- state (.mt_ram: neither loaded nor zeroed; the patcher places it) ----------- */

#define VZ_MAGIC            0x5A56544Du     /* "MTVZ" */

struct ripple {
    float x, y;                             /* centre, pads */
    float born;                             /* take clock, seconds */
    float speed;                            /* pads per second */
    float life;                             /* seconds */
    float amp, width, stretch;              /* stretch: x distance scale */
    uint8_t kind;                           /* 0 a ring, 1 a ring and a glow, 2 two rings */
    uint8_t live;
    uint8_t pad[2];
};                                          /* 36 bytes */

struct vz_state {
    uint32_t magic;
    /* audio side */
    float clock;                            /* seconds since the take started */
    float phase[3], target[3];              /* smoke phase offsets and where they ease to */
    float wind;                             /* ripple centres drift along x */
    uint32_t hash;                          /* the take's history so far */
    float last_ripple;                      /* take clock of the newest ripple */
    uint8_t was_playing;
    uint8_t next_ripple;
    uint8_t take;                           /* counts takes (the screen side resets on a new one) */
    uint8_t pad0;
    struct ripple rip[RIPPLES];
    /* screen side */
    uint32_t last_update;                   /* ms: last Pattern screen update */
    uint32_t last_frame;                    /* ms */
    uint32_t last_button;                   /* ms: last column button event */
    uint8_t armed;                          /* switched on by the four buttons */
    uint8_t showing;                        /* the pads are the visualizer's */
    uint8_t down;                           /* column buttons held, bit per column */
    uint8_t eat;                            /* presses not passed on: their releases aren't either */
    uint8_t chord;                          /* not passing presses on until all four are up */
    uint8_t saved_col;                      /* the column before the first press */
    uint8_t pad1[2];
    uint8_t sent[PADS];                     /* level last written per pad (0xFF: none) */
    uint8_t dirty_lo, dirty_hi;             /* PWM slots changed since the last flush */
    uint8_t full_flush;                     /* the next flush is the stock one (enables too) */
    uint8_t pad2;
};
static struct vz_state vz_state_ram __attribute__((section(".mt_ram")));
#define VZ (&vz_state_ram)

static void vz_reset_take(void)
{
    VZ->clock = 0.0f;
    for (int i = 0; i < 3; i++)
        VZ->phase[i] = VZ->target[i] = 0.0f;
    VZ->wind = 0.0f;
    VZ->hash = 0x9E3779B9u;
    VZ->last_ripple = -100.0f;
    VZ->next_ripple = 0;
    for (uint32_t i = 0; i < RIPPLES; i++)
        VZ->rip[i].live = 0;
}

static void vz_init_if_needed(void)         /* RAM outside .bss holds garbage at power-on */
{
    if (VZ->magic == VZ_MAGIC)
        return;
    vz_reset_take();
    VZ->was_playing = 0;
    VZ->take = 0;
    VZ->last_update = VZ->last_frame = VZ->last_button = 0;
    VZ->armed = VZ->showing = 0;
    VZ->down = VZ->eat = VZ->chord = 0;
    VZ->saved_col = 0;
    for (uint32_t i = 0; i < PADS; i++)
        VZ->sent[i] = 0xFF;
    VZ->dirty_lo = 0xFF;
    VZ->dirty_hi = 0;
    VZ->full_flush = 1;
    VZ->magic = VZ_MAGIC;
}

/* ---- small maths ----------------------------------------------------------------- */

#define PI_F                3.14159265f

static float vz_sin(float x)                /* about 0.1% error: plenty for brightness */
{
    float n = x * (1.0f / (2.0f * PI_F));
    n = (float)(int32_t)(n + (n >= 0.0f ? 0.5f : -0.5f));
    x -= n * (2.0f * PI_F);
    float ax = x < 0.0f ? -x : x;
    float y = (4.0f / PI_F) * x - (4.0f / (PI_F * PI_F)) * x * ax;
    float ay = y < 0.0f ? -y : y;
    return 0.225f * (y * ay - y) + y;
}

static float vz_sqrt(float x)
{
    float r;
    __asm__("vsqrt.f32 %0, %1" : "=t"(r) : "t"(x));
    return r;
}

static float vz_abs(float x) { return x < 0.0f ? -x : x; }

static uint32_t vz_mix(uint32_t h)          /* integer hash (murmur3 finaliser) */
{
    h ^= h >> 16;
    h *= 0x85EBCA6Bu;
    h ^= h >> 13;
    h *= 0xC2B2AE35u;
    h ^= h >> 16;
    return h;
}

static float vz_unit(uint32_t h, uint32_t shift) /* -0.5..0.5 from 8 bits of h */
{
    return (float)((h >> shift) & 0xFFu) * (1.0f / 255.0f) - 0.5f;
}

static float vz_tempo_scale(void)           /* 1 at 120 BPM */
{
    float t = *FW_TEMPO;
    if (!(t >= 20.0f && t <= 400.0f))
        t = 120.0f;
    return t * (1.0f / 120.0f);
}

/* ---- the audio side -------------------------------------------------------------- */

/* A track has just moved to a step: its note becomes a ripple, and the note, the
 * instrument and the FX nudge the smoke. */
static void vz_step(const uint8_t *seq, uint32_t track)
{
    const uint8_t *pat = *(const uint8_t *const *)(seq + SEQ_PATTERN);
    if (!pat || FW_MUTE[track])
        return;
    uint32_t step = *(const volatile uint16_t *)(seq + SEQ_TRACK(track) + TRK_STEP);
    if (step > 127u)
        return;
    const uint8_t *s = pat + PAT_TRACK(track) + 1u + STEP_SIZE * step;
    uint32_t note = s[0], instr = s[1], f1t = s[2], f1v = s[3], f2t = s[4], f2v = s[5];
    int has_note = note < 128u;
    if (!has_note && !f1t && !f2t)
        return;

    uint32_t e = vz_mix((track << 24) ^ (note << 16) ^ (instr << 8) ^ (f1t * 0x9Du) ^ (f1v * 0x3B1u)
                        ^ (f2t * 0x7F4Bu) ^ (f2v * 0x2C9277u));
    VZ->hash = vz_mix(VZ->hash ^ e);        /* the history: differs on every pass */
    uint32_t h = VZ->hash;
    float push = has_note ? 0.5f : 0.25f;   /* FX-only steps stir the smoke less */
    VZ->target[0] += vz_unit(h, 0) * push;
    VZ->target[1] += vz_unit(h, 8) * push;
    VZ->target[2] += vz_unit(h, 16) * push;
    VZ->wind += vz_unit(h, 24) * 0.35f;
    if (VZ->wind > 1000.0f || VZ->wind < -1000.0f)
        VZ->wind = 0.0f;
    if (!has_note)
        return;

    /* A new ripple only into a free slot (none is cut short) and not within half a
     * beat of the last one: a busy pattern gives a steady flow, not a flicker. */
    float tempo = vz_tempo_scale();
    if (VZ->clock - VZ->last_ripple < RIPPLE_GAP_BEATS * 0.5f / tempo)
        return;
    struct ripple *r = 0;
    for (uint32_t i = 0; i < RIPPLES && !r; i++) {
        struct ripple *c = &VZ->rip[(VZ->next_ripple + i) % RIPPLES];
        if (!c->live || VZ->clock - c->born > c->life)
            r = c;
    }
    if (!r)
        return;
    VZ->next_ripple = (uint8_t)((uint32_t)(r - VZ->rip + 1) % RIPPLES);
    VZ->last_ripple = VZ->clock;
    float x = (float)(note % PAD_COLS) + VZ->wind;
    x -= (float)(int32_t)(x * (1.0f / PAD_COLS)) * PAD_COLS;
    if (x < 0.0f)
        x += PAD_COLS;
    r->x = x;
    r->y = (float)(PAD_ROWS - 1u - (note / PAD_COLS) % PAD_ROWS);
    r->born = VZ->clock;
    r->speed = (0.45f + (float)(instr % 7u) * 0.12f) * tempo;
    r->life = (4.5f + (float)(track % 4u) * 0.6f) / (tempo < 0.5f ? 0.5f : tempo);
    r->amp = 0.75f;
    r->width = 1.5f;
    r->stretch = 1.0f;
    uint32_t types[2] = {f1t, f2t}, values[2] = {f1v, f2v};
    for (int i = 0; i < 2; i++) {
        if (!types[i])
            continue;
        if (types[i] == FX_VELOCITY) {
            float v = (float)(values[i] > 100u ? 100u : values[i]) * 0.01f;
            r->amp = 0.2f + 0.55f * v;
        } else {
            r->width = 1.1f + (float)(values[i] % 6u) * 0.15f;
            r->stretch = 0.45f + (float)(types[i] % 4u) * 0.3f;
        }
    }
    r->kind = (uint8_t)(track % 3u);
    r->live = 1;
}

/* replaces "bl seq_advance" (veneer 0x600aa858) at 0x600aa7f8 in app_audio_block (chained) */
void mt_vz_seq_advance(void *seq, float seconds)
{
    vz_init_if_needed();
    uint8_t *s = (uint8_t *)seq;
    int playing = s[SEQ_PLAYING] != 0;
    if (playing && !VZ->was_playing) {      /* a new take: everything from the start */
        vz_reset_take();
        VZ->take++;
    }
    VZ->was_playing = (uint8_t)playing;
    ((advance_fn)mt_vz_next_seq_advance)(seq, seconds);
    if (!playing || !(seconds > 0.0f && seconds < 0.1f))
        return;
    VZ->clock += seconds;
    if (!*(volatile uint8_t *)&VZ->showing)  /* hidden: the clock only, no animation work */
        return;
    float k = SMOKE_EASE * seconds;
    for (int i = 0; i < 3; i++)
        VZ->phase[i] += (VZ->target[i] - VZ->phase[i]) * k;
}

/* the word the sequencer's ITCM code jumps through to seq_advance_step(seq, track),
 * 0x601acf00 in flash (chained) */
uint32_t mt_vz_advance_step(void *seq, uint32_t track)
{
    uint32_t r = ((advance_step_fn)mt_vz_next_advance_step)(seq, track);
    const uint8_t *s = (const uint8_t *)seq;
    if (VZ->magic == VZ_MAGIC && *(volatile uint8_t *)&VZ->showing && track < TRACKS
        && s[SEQ_PLAYING] && !s[SEQ_PERFORMANCE])
        vz_step(s, track);
    return r;
}

/* ---- the pads -------------------------------------------------------------------- */

static uint32_t vz_ms(void)
{
    void *t = *FW_TIMER;
    if (!t)
        return 0;
    return ((ms_fn)(**(uint32_t **)t))(t);
}

static void *vz_leds(void)
{
    return *(void *const volatile *)(FW_PAD_MGR + MGR_LEDS);
}

/* While it shows, every pad stays enabled (dark is brightness 0), so only brightness
 * bytes change; the slots that did are noted for mt_vz_flush. */
static void vz_put(uint32_t pad, uint32_t level)
{
    void *leds = vz_leds();
    if (!leds)
        return;
    ((set_pad_fn)mt_vz_next_set_pad)(leds, pad, VZ->showing ? 1u : level != 0, level);
    if (VZ->showing) {
        uint32_t slot = pad + pad / PAD_COLS * 4u;
        if (slot < VZ->dirty_lo)
            VZ->dirty_lo = (uint8_t)slot;
        if (slot > VZ->dirty_hi)
            VZ->dirty_hi = (uint8_t)slot;
    }
}

/* Back to the stock pads: what the pad manager's layers hold now. */
static void vz_hide(void)
{
    VZ->showing = 0;
    for (uint32_t p = 0; p < PADS; p++) {
        uint32_t a = FW_PAD_MGR[MGR_LAYER_A + p], b = FW_PAD_MGR[MGR_LAYER_B + p];
        vz_put(p, a > b ? a : b);
        VZ->sent[p] = 0xFF;
    }
}

/* the pad LED driver's flush (vtable word 0x600373b8, chained), called from the main
 * loop while a pad has changed: while the visualizer shows, only the changed
 * brightness bytes go out, in one burst after the page select */
uint32_t mt_vz_flush(void *driver)
{
    uint8_t *d = (uint8_t *)driver;
    if (VZ->magic == VZ_MAGIC && VZ->showing && !VZ->full_flush && d[DRV_DIRTY]) {
        void *bus = *(void **)(d + DRV_BUS);
        i2c_write_fn write = bus ? (i2c_write_fn)(*(uint32_t **)bus)[BUS_WRITE / 4u] : 0;
        uint32_t lo = VZ->dirty_lo, hi = VZ->dirty_hi;
        if (write && (lo > hi || hi >= 144u
                      || (write(bus, d[DRV_ADDR], CHIP_PAGE_REG, 1, d + DRV_PAGE, 1) == 0
                          && write(bus, d[DRV_ADDR], CHIP_PWM_REG + lo, 1, d + DRV_PWM + lo, hi - lo + 1u) == 0))) {
            VZ->dirty_lo = 0xFF;
            VZ->dirty_hi = 0;
            d[DRV_DIRTY] = 0;
            return 1;
        }
        VZ->full_flush = 1;                 /* a write failed: the stock flush recovers */
    }
    uint32_t r = ((flush_fn)mt_vz_next_flush)(driver);
    if (VZ->magic == VZ_MAGIC && r && !d[DRV_DIRTY]) {
        VZ->full_flush = 0;                 /* everything sent: from now on, the short way */
        VZ->dirty_lo = 0xFF;
        VZ->dirty_hi = 0;
    }
    return r;
}

/* the pad LED object's setPad (vtable word 0x60038298, chained): the firmware's own pad
 * writes are dropped while the visualizer shows */
void mt_vz_set_pad(void *leds, uint32_t pad, uint32_t on, uint32_t level)
{
    if (VZ->magic == VZ_MAGIC && VZ->showing) {
        if (vz_ms() - VZ->last_update <= SCREEN_GONE_MS)
            return;
        vz_hide();                          /* the Pattern screen is gone: give the pads back */
    }
    ((set_pad_fn)mt_vz_next_set_pad)(leds, pad, on, level);
}

/* ---- drawing --------------------------------------------------------------------- */

static float vz_smoke(float x, float y, float beats)
{
    /* three slow waves, each bending the next: soft swirling bands, like smoke */
    float w1 = vz_sin(0.36f * x + 0.56f * y + 0.22f * beats + VZ->phase[0]);
    float w2 = vz_sin(-0.22f * x + 0.84f * y - 0.15f * beats + VZ->phase[1] + 1.1f * w1);
    float w3 = vz_sin(0.16f * x - 0.63f * y + 0.10f * beats + VZ->phase[2] + 1.4f * w2);
    float s = 0.5f + 0.5f * w3;
    return s * s * s;                       /* thin bright wisps, dark between */
}

static float vz_ripples(float x, float y, float now)
{
    float v = 0.0f;
    for (uint32_t i = 0; i < RIPPLES; i++) {
        const struct ripple *r = &VZ->rip[i];
        if (!r->live)
            continue;
        float age = now - r->born;
        if (age < 0.0f || age > r->life)
            continue;
        float dx = vz_abs(x - r->x);
        if (dx > PAD_COLS * 0.5f)
            dx = PAD_COLS - dx;             /* the pads wrap around left to right */
        dx *= r->stretch;
        float dy = y - r->y;
        float d = vz_sqrt(dx * dx + dy * dy);
        float fade = 1.0f - age / r->life;
        float rise = age < RIPPLE_RISE ? age * (1.0f / RIPPLE_RISE) : 1.0f;
        float env = r->amp * fade * fade * rise * rise * (3.0f - 2.0f * rise);   /* smoothstep in */
        float rad = r->speed * age;
        float ring = 1.0f - vz_abs(d - rad) / r->width;
        if (ring > 0.0f)
            v += env * ring * ring;
        if (r->kind == 1) {                 /* a glow inside the ring */
            float g = 1.0f - d / (rad + 0.6f);
            if (g > 0.0f)
                v += 0.45f * env * g;
        } else if (r->kind == 2) {          /* a second, slower ring */
            float ring2 = 1.0f - vz_abs(d - 0.55f * rad) / r->width;
            if (ring2 > 0.0f)
                v += 0.6f * env * ring2 * ring2;
        }
    }
    return v;
}

static void vz_draw(void)
{
    uint32_t top = FW_SETTINGS[SET_PADS_BRIGHT], dim = FW_SETTINGS[SET_PADS_DIM];
    if (dim > top)
        top = dim;
    if (top > LEVEL_MAX)
        top = LEVEL_MAX;
    float now = VZ->clock;
    float beats = now * 2.0f * vz_tempo_scale();    /* 120 BPM: 2 beats a second */
    for (uint32_t p = 0; p < PADS; p++) {
        float x = (float)(p % PAD_COLS), y = (float)(p / PAD_COLS);
        float v = SMOKE_LEVEL * vz_smoke(x, y, beats) + vz_ripples(x, y, now);
        v = 1.5f * v / (1.0f + 0.5f * v);   /* soft limit: overlaps glow, not clip */
        if (v > 1.0f)
            v = 1.0f;
        uint32_t level = (uint32_t)(v * (float)top + 0.5f);
        if (level != VZ->sent[p]) {
            vz_put(p, level);
            VZ->sent[p] = (uint8_t)level;
        }
    }
}

/* Playing in the Pattern screen, record off, no bottom-row pad view, not performance mode. */
static int vz_can_show(uint32_t now)
{
    return FW_SEQ[SEQ_PLAYING] && !FW_SEQ[SEQ_PERFORMANCE] && !FW_PATTERN_FIELDS[PS_RECORD]
           && (int8_t)FW_PATTERN_FIELDS[PS_PAD_MODE] < 0 && now - VZ->last_update <= SCREEN_GONE_MS;
}

/* Pattern screen vtable slot 0x600403dc (update method 0x600e1f99; chained) */
void mt_vz_pattern_update(void *screen, void *a1, void *a2, void *a3)
{
    ((update_fn)mt_vz_next_pattern_update)(screen, a1, a2, a3);
    vz_init_if_needed();
    uint32_t now = vz_ms();
    if (now - VZ->last_update > SCREEN_GONE_MS) {   /* back from another screen */
        VZ->down = VZ->eat = VZ->chord = 0;
        if (VZ->showing)
            vz_hide();
    }
    VZ->last_update = now;
    int show = VZ->armed && vz_can_show(now);
    if (show && !VZ->showing) {
        VZ->showing = 1;
        VZ->full_flush = 1;                 /* the first frame enables every pad: send it all */
        for (uint32_t i = 0; i < PADS; i++)
            VZ->sent[i] = 0xFF;
        VZ->last_frame = now - FRAME_MS;
    } else if (!show && VZ->showing) {
        vz_hide();
    }
    if (VZ->showing && now - VZ->last_frame >= FRAME_MS) {
        VZ->last_frame = now;
        vz_draw();
    }
}

/* ---- the column buttons ---------------------------------------------------------- */

static void vz_call(uint32_t col, uint32_t state)
{
    static const volatile uint32_t *const next[4] = {
        &mt_vz_next_note, &mt_vz_next_instrument, &mt_vz_next_fx1, &mt_vz_next_fx2};
    ((button_fn)*next[col])(state);
}

static void vz_cancel_long_press(void)
{
    for (int i = 0; i < 4; i++)
        FW_PATTERN_FIELDS[PS_HELD + i] = 0;
}

static void vz_button(uint32_t col, uint32_t state)
{
    vz_init_if_needed();
    uint8_t bit = (uint8_t)(1u << col);
    uint32_t now = vz_ms();
    if (state > 1u) {
        vz_call(col, state);
        return;
    }
    if (now - VZ->last_button > BUTTONS_STALE_MS && !VZ->showing)
        VZ->down = VZ->eat = VZ->chord = 0;  /* a release went missing: start afresh */
    VZ->last_button = now;

    if (state == 0u) {                      /* release */
        VZ->down &= (uint8_t)~bit;
        if (!VZ->down)
            VZ->chord = 0;
        if (VZ->eat & bit) {
            VZ->eat &= (uint8_t)~bit;
            return;
        }
        vz_call(col, 0);
        return;
    }

    /* press */
    if (!VZ->down)
        VZ->saved_col = FW_PATTERN_FIELDS[PS_COLUMN];
    VZ->down |= bit;
    if (VZ->showing || VZ->chord) {         /* switching off, or the rest of that chord */
        if (VZ->showing) {
            VZ->armed = 0;
            vz_hide();
        }
        VZ->chord = 1;
        VZ->eat |= bit;
        return;
    }
    if (VZ->armed && !vz_can_show(now))
        VZ->armed = 0;                      /* armed but not showing: the button works as usual */
    if (!vz_can_show(now)) {
        vz_call(col, 1);
        return;
    }
    if (VZ->down == 0x0Fu) {                /* all four: switch on */
        VZ->eat |= bit;
        VZ->chord = 1;
        uint32_t saved = VZ->saved_col;
        if (saved < 4u && FW_PATTERN_FIELDS[PS_COLUMN] != saved) {
            vz_cancel_long_press();
            vz_call(saved, 1);              /* select the column from before again */
        }
        vz_cancel_long_press();
        VZ->armed = 1;
        return;
    }
    vz_call(col, 1);
    if (VZ->down & (VZ->down - 1u))         /* two or more held: no long-press action */
        vz_cancel_long_press();
}

/* Pattern screen button callbacks (flash literals 0x600df2dc..0x600df2e8) */
void mt_vz_note(uint32_t state) { vz_button(0, state); }
void mt_vz_instrument(uint32_t state) { vz_button(1, state); }
void mt_vz_fx1(uint32_t state) { vz_button(2, state); }
void mt_vz_fx2(uint32_t state) { vz_button(3, state); }

/* ---- version text: Config shows 1.2.0-<VIS_VERSION> ------------------------------- */
const char mt_version_fmt[] = "%d.%d.%d-" VIS_VERSION;
