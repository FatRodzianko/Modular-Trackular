/*
 * humanize: small random Volume/Velocity and Micro-move FX for the notes in the
 * pattern editor's selection (Polyend Tracker+ firmware 1.2.0).
 *
 * The Pattern screen's bottom bar has three pages (More cycles through them). Page 3
 * has Pattern Name, Track Name, five unused buttons and More. This patch puts a
 * "Humanize" button on the first unused one (button 2):
 *  - mt_hz_bind replaces the call that binds button 0 on page 3 (0x600e29ba): it binds
 *    button 0 as before, then button 2 to mt_hz_button.
 *  - mt_hz_label / mt_hz_colors / mt_hz_data replace three calls in page 3's label
 *    code (0x600e584c): button 2's text becomes "Humanize", and it gets Track Name's
 *    data and colors, so it looks like an active button.
 *
 * Like the stock editing buttons (Fill, Undo, Redo, page 2), Humanize only works in
 * record mode: without it the button is left unbound and drawn in the grey color set.
 * Toggling record redraws the current page (0x600e2a94 -> pat_show_page), which
 * runs these hooks again.
 *
 * mt_hz_button does what the stock Invert button does around its edit: push the
 * pattern onto the undo ring (so Undo reverts it), turn the editor's selection into
 * the sequencer's step/track rectangle, edit, rebuild the grid rows and mark the
 * pattern changed (so it gets saved). Without a selection it takes the cursor's
 * whole track, up to the pattern length.
 *
 * For every step with a note (not empty, note off, cut or fade):
 *  - an existing Volume/Velocity FX moves by a random -5..+5, kept within 1..100
 *    (an existing V 0 stays 0);
 *  - an existing Micro-move FX moves by -5..+5, kept within 0..100;
 *  - a missing Micro-move goes into the first free FX slot as m 0..10;
 *  - a missing Volume/Velocity then goes into a free slot as V 45..55.
 * Steps without a free slot keep what they have. Random numbers come from the
 * firmware's own generator (the one its random FX use).
 *
 * No RAM of its own: everything is on the stack.
 */
#include <stdint.h>

#ifndef HUMANIZE_VERSION
#define HUMANIZE_VERSION "hz2"
#endif

/* ---- firmware 1.2.0 addresses (function pointers carry the Thumb bit) ----------- */
#define FW_FM               0x2001E488u     /* file manager object */
#define FW_SEQ              0x20017274u     /* sequencer: bytes 0..3 = selection rectangle */
#define FW_SEQ_PATTERN      (FW_SEQ + 0x14u)    /* -> current pattern data (.mtp layout) */
#define FW_PATVIEW          0x20021770u     /* Pattern screen object */
#define FW_ED               (FW_PATVIEW + 0x1000u)  /* its editor state */

#define FW_UNDO_PUSH        0x600C3D91u     /* (fm): copy the current pattern onto the undo ring */
#define FW_SEL_TO_SEQ       0x600E0B59u     /* (): editor selection, or the cursor, -> FW_SEQ[0..3] */
#define FW_FILL_ROWS        0x600DF3A1u     /* (patview): rebuild the grid's row texts */
#define FW_MARK_CHANGED     0x600BCCD9u     /* (fm): mark the current pattern as changed */
#define FW_RAND_RANGE       0x600AF0E9u     /* (lo, hi): lo .. hi-1 (Park-Miller) */
#define FW_BIND_BUTTON      0x600CF6EDu     /* (manager, index, flag, handler) */
#define FW_SET_TEXT         0x60101D5Du     /* disp_ctrl_set_text(display, ctrl, text) */
#define FW_SET_COLORS       0x60101DA5u     /* disp_ctrl_set_colors(display, ctrl, colors) */
#define FW_SET_DATA         0x60101DF9u     /* disp_ctrl_set_data(display, ctrl, data) */

/* editor state (FW_ED + offset) */
#define ED_CURSOR_TRACK     0x1E4u
#define ED_SELECTION_MODE   0x1F4u          /* 2 = a selection is active */
#define ED_RECORD           0x254u          /* record mode: editing buttons are active */
#define ED_BUTTON_CTRL      0x292u          /* int8 control index of bottom-bar button 0..7 */

/* display: *(display + 8) -> color sets for the bottom-bar labels */
#define DISP_STYLES         8u
#define STYLE_BUTTON        0x44u           /* active button (Track Name, page 2 in record mode) */
#define STYLE_BUTTON_OFF    0x54u           /* greyed out (page 2 outside record mode) */

/* pattern data: 0x1c-byte header, then 16 tracks of 0x301 bytes (last-step byte,
 * then 128 steps of note, instrument, fx1 type, fx1 value, fx2 type, fx2 value) */
#define PAT_LAST_STEP       0x1Cu           /* track 0's byte: pattern length - 1 */
#define PAT_STEPS           0x1Du
#define PAT_TRACK_SIZE      0x301u
#define PAT_TRACKS          16u
#define PAT_MAX_STEPS       128u

#define FX_NONE             0u
#define FX_MICRO_MOVE       2u
#define FX_VOLUME           18u

#define HZ_BUTTON           2u              /* page 3, first unused button */
#define HZ_AMOUNT           10              /* new m: 0..AMOUNT; changes: +-AMOUNT/2 */
#define HZ_HALF             (HZ_AMOUNT / 2)
#define HZ_VOLUME_CENTER    50

typedef void (*void_fn)(void);
typedef void (*obj_fn)(uint32_t obj);
typedef int (*rand_fn)(int lo, int hi);
typedef void (*bind_fn)(void *manager, uint32_t index, uint32_t flag, void_fn handler);
typedef void (*ctrl_fn)(void *display, int ctrl, const void *arg);

void mt_hz_button(void);

/* ---- the edit ----------------------------------------------------------------- */

static int rnd(int lo, int hi)                  /* lo .. hi, inclusive */
{
    return ((rand_fn)FW_RAND_RANGE)(lo, hi + 1);
}

static uint8_t clamp(int v, int lo, int hi)
{
    return (uint8_t)(v < lo ? lo : v > hi ? hi : v);
}

static void add_fx(uint8_t *step, uint8_t type, uint8_t value)
{
    for (uint32_t k = 0; k < 2; k++) {
        uint8_t *fx = step + 2 + 2 * k;
        if (fx[0] == FX_NONE) {
            fx[0] = type;
            fx[1] = value;
            return;
        }
    }
}

static void humanize_step(uint8_t *step)
{
    if ((int8_t)step[0] < 0)                    /* empty (-1), or note off / cut / fade */
        return;
    int has_micro = 0, has_volume = 0;
    for (uint32_t k = 0; k < 2; k++) {
        uint8_t *fx = step + 2 + 2 * k;
        if (fx[0] == FX_VOLUME) {
            has_volume = 1;
            if (fx[1] != 0)                     /* V 0 is a deliberate silent note */
                fx[1] = clamp(fx[1] + rnd(-HZ_HALF, HZ_HALF), 1, 100);
        } else if (fx[0] == FX_MICRO_MOVE) {
            has_micro = 1;
            fx[1] = clamp(fx[1] + rnd(-HZ_HALF, HZ_HALF), 0, 100);
        }
    }
    if (!has_micro)
        add_fx(step, FX_MICRO_MOVE, (uint8_t)rnd(0, HZ_AMOUNT));
    if (!has_volume)
        add_fx(step, FX_VOLUME, clamp(HZ_VOLUME_CENTER + rnd(-HZ_HALF, HZ_HALF), 1, 100));
}

static int record_mode(void)
{
    return *(const volatile uint8_t *)(FW_ED + ED_RECORD) != 0;
}

void mt_hz_button(void)
{
    if (!record_mode())                         /* only bound in record mode; just in case */
        return;
    ((obj_fn)FW_UNDO_PUSH)(FW_FM);
    ((void_fn)FW_SEL_TO_SEQ)();

    const volatile uint8_t *ed = (const volatile uint8_t *)FW_ED;
    const volatile uint8_t *sel = (const volatile uint8_t *)FW_SEQ;
    uint8_t *data = *(uint8_t **)FW_SEQ_PATTERN;
    uint32_t last = data[PAT_LAST_STEP];
    if (last >= PAT_MAX_STEPS)
        last = PAT_MAX_STEPS - 1;

    uint32_t s0, s1, t0, t1;
    if (ed[ED_SELECTION_MODE] == 2) {
        s0 = sel[0];
        t0 = sel[1];
        s1 = sel[2];
        t1 = sel[3];
    } else {                                    /* no selection: the cursor's whole track */
        t0 = t1 = ed[ED_CURSOR_TRACK];
        s0 = 0;
        s1 = last;
    }
    if (s1 >= PAT_MAX_STEPS)
        s1 = PAT_MAX_STEPS - 1;
    if (t1 >= PAT_TRACKS)
        t1 = PAT_TRACKS - 1;

    for (uint32_t t = t0; t <= t1; t++)
        for (uint32_t s = s0; s <= s1; s++)
            humanize_step(data + PAT_STEPS + PAT_TRACK_SIZE * t + 6 * s);

    ((obj_fn)FW_FILL_ROWS)(FW_PATVIEW);
    ((obj_fn)FW_MARK_CHANGED)(FW_FM);
}

/* ---- the button ---------------------------------------------------------------- */

static int hz_ctrl(void)
{
    return *(const volatile int8_t *)(FW_ED + ED_BUTTON_CTRL + HZ_BUTTON);
}

/* replaces "bl 0x600cf6ec" at 0x600e29ba (page 3: bind button 0 = Pattern Name).
 * The page's binding has just cleared buttons 0..6, so outside record mode button 2
 * stays unbound. */
void mt_hz_bind(void *manager, uint32_t index, uint32_t flag, void_fn handler)
{
    ((bind_fn)FW_BIND_BUTTON)(manager, index, flag, handler);
    if (record_mode())
        ((bind_fn)FW_BIND_BUTTON)(manager, HZ_BUTTON, 1, mt_hz_button);
}

/* replaces "bl disp_ctrl_set_text" at 0x600e5884 (page 3: button 2's empty text) */
void mt_hz_label(void *display, int ctrl, const void *text)
{
    (void)text;
    ((ctrl_fn)FW_SET_TEXT)(display, ctrl, "Humanize");
}

/* replaces "bl disp_ctrl_set_colors" at 0x600e593c (page 3: Track Name's colors).
 * Button 2 gets the active set in record mode and the greyed-out set otherwise. */
void mt_hz_colors(void *display, int ctrl, const void *colors)
{
    ((ctrl_fn)FW_SET_COLORS)(display, ctrl, colors);
    const uint8_t *styles = *(const uint8_t *const *)((const uint8_t *)display + DISP_STYLES);
    ((ctrl_fn)FW_SET_COLORS)(display, hz_ctrl(),
                             styles + (record_mode() ? STYLE_BUTTON : STYLE_BUTTON_OFF));
}

/* replaces "bl disp_ctrl_set_data" at 0x600e5966 (page 3: Track Name's data) */
void mt_hz_data(void *display, int ctrl, const void *data)
{
    ((ctrl_fn)FW_SET_DATA)(display, ctrl, data);
    ((ctrl_fn)FW_SET_DATA)(display, hz_ctrl(), data);
}

/* ---- version text: Config shows 1.2.0-<HUMANIZE_VERSION> ------------------------ */
const char mt_version_fmt[] = "%d.%d.%d-" HUMANIZE_VERSION;
