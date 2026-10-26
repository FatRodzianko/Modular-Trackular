/*
 * polymeter: a length per track (Polyend Tracker+ firmware 1.2.0).
 *
 * The idea comes from a custom firmware for the Tracker Mini shown on r/PolyendTracker
 * (each track has its own length; steps past it are dimmed; all tracks line up again at
 * the end of the pattern). This is a re-implementation from that description; nothing
 * of it was seen or used. docs/POLYMETER_PLAN.md has the investigation and the plan.
 *
 * A track with length E shorter than the pattern plays its steps 0..E-1 over and over,
 * and starts again with every other track when the pattern ends: in a 16-step pattern a
 * track of length 6 plays 0-5, 0-5, 0-3. The sequencer itself is left alone. Every
 * track's position (+0x88) still counts the whole pattern, so the pattern end, song
 * mode's next-pattern preload, the metronome and the Pattern screen's play row (which
 * all read track 1's position) behave as in stock firmware. Only the reads of a track's
 * step data change:
 *  - seq_process_track (ITCM) copies a step's 6 bytes into the track's work record at
 *    micro-step 1 with memcpy_small at ITCM 0x2170. mt_pm_step_copy reads step
 *    (position mod E) instead. Every play mode goes through it: pattern loop, song mode,
 *    Play from a position, a looping selection, performance mode;
 *  - Roll looks ahead for the next note (seq_distance_to_next_note_q, ITCM 0x3958,
 *    called at 0x33a2 and 0x3520) to know how long the roll lasts. mt_pm_roll_a/_b look
 *    at the same steps mod E, up to the pattern's end as stock does.
 *
 * E(t) is the track's length from the table of the playing pattern (project +0x6c8)
 * when it is 1..pattern length - 1; anything else (0 = none, or a length the pattern
 * has been shortened below) is the pattern's length, which is exactly stock. The table
 * is read in the audio interrupt; until it is set up (the magic word) nothing changes.
 *
 * Setting a length: the Pattern screen's page 3 gets a "Trk Len" button, on the first
 * button after the other patches' (pm_button: 2, or 3 or 4 after Humanize and Copy to
 * New). While it is held, the encoder changes the cursor's track's length, 1..the
 * pattern's length, and the button's second line shows it ("--" when the track follows
 * the pattern). Released, the encoder is stock's again.
 *  - mt_pm_bind chains page 3's bind of button 1 (0x600e29c6, shared with copypattern)
 *    and binds Trk Len's button as kind 3 (called on press and on release);
 *  - mt_pm_label / mt_pm_label2 chain button 6's two (empty) text calls in page 3's
 *    labels (0x600e58b4, 0x600e5914), the last of buttons 2..6, and then give Trk Len's
 *    button its texts, over the empty ones stock has just set;
 *  - mt_pm_colors / mt_pm_data chain More's colors and data calls (0x600e594a,
 *    0x600e5976), the only ones after the texts, and give Trk Len's button the active
 *    colors and Pattern Name's data, as page 3 gives buttons 0 and 1;
 *  - mt_pm_jog takes the Pattern screen's encoder word (0x600df2f0, pat_jog 0x600c90a1)
 *    while the button is held and still bound to this patch, and redraws the grid.
 *
 * The Pattern grid shows a short track's steps E.. in each column's empty color, the one
 * stock gives a "-" cell, so they read as "nothing plays here" with their contents still
 * visible. The grid control picks each cell's color into ctrl +0x44 and draws its text
 * with patgrid_draw_cell (0x6010b204); mt_pm_cell_* chain its 5 calls (note, instrument
 * twice, FX1, FX2) and swap the color for the call. Only the column's normal color is
 * swapped, so empty cells, muted tracks, the cursor and a selection look as in stock.
 *
 * Shared hooks go through chain words (mt_pm_next_*) that tools/patch_ptf.py fills with
 * what the site pointed to before, so other patches can hook them too.
 */
#include <stdint.h>

#ifndef POLYMETER_VERSION
#define POLYMETER_VERSION "pm1"
#endif

/* ---- firmware 1.2.0 addresses ------------------------------------------------------ */
#define FW_PROJECT_PATTERN  ((const volatile uint8_t *)0x202046C8u)  /* project +0x6c8: playing pattern */

/* sequencer object */
#define SEQ_PATTERN         0x14u           /* the playing pattern's buffer (.mtp layout) */
#define SEQ_TRACK(t)        (0x68u * (t))
#define TRK_STEP            0x88u           /* uint16: the track's position in the pattern */

/* pattern buffer */
#define PAT_LAST_STEP       0x1Cu           /* track 1's last-step byte: the pattern's length - 1 */
#define PAT_STEPS(t)        (0x1Du + 0x301u * (t))  /* track t's step 0; 6 bytes a step, the note first */
#define STEP_SIZE           6u
#define NOTE_EMPTY          (-1)

#define TRACKS              16u
#define PATTERNS            256u            /* indexed by pattern number (1..255) */

/* Pattern screen */
#define FW_SEQ              0x20017274u
#define FW_PATVIEW          0x20021770u     /* Pattern screen object */
#define FW_ED               (FW_PATVIEW + 0x1000u)  /* its editor state */
#define ED_CURSOR_TRACK     0x1E4u          /* uint8: the cursor's track */
#define ED_BUTTON_CTRL      0x292u          /* int8 control index of bottom-bar button 0..7 */
#define ED_OVERLAY          0x398u          /* an overlay is open: pat_jog does nothing */
#define ED_GRID_CENTRE      0x1E8u          /* uint16: the step on the grid's middle row */
#define ED_GRID_LENGTH      0x1EAu          /* uint16: the pattern's length, as the grid was filled */
#define ED_GRID_CTRL        0x29Bu          /* int8 control index of the grid */
#define PATVIEW_NAME_DATA   0x12CCu         /* page 3: Pattern Name's (button 0's) data */
#define FW_DISPLAY          0x20027D64u
#define DISP_STYLES         8u
#define STYLE_BUTTON        0x44u           /* active button colors */
#define FW_EMPTY_TEXT       ((const char *)0x60038660u)     /* "" */
#define FW_JOG_GUARD_ARG    0x200209ACu     /* pat_jog does nothing while ... */
#define FW_JOG_GUARD        0x6010E0F9u     /* ... this (arg) is nonzero (q) */

/* button manager *0x200209e0: count +5, 20-byte entries at *(+0x1c): kind +0, kind 3's handler +8 */
#define FW_BUTTON_MANAGER   ((uint8_t *const volatile *)0x200209E0u)
#define BTN_COUNT           5u
#define BTN_ENTRIES         0x1Cu
#define BTN_ENTRY_SIZE      0x14u
#define BTN_KIND3           3u
#define BTN_KIND3_HANDLER   8u
#define FW_BIND_KIND3       0x600CF74Du     /* btn_bind_kind3(manager, index, handler) */
#define FW_SET_TEXT         0x60101D5Du     /* disp_ctrl_set_text(display, ctrl, text) */
#define FW_SET_TEXT2        0x60101D75u     /* disp_ctrl_set_ptr38(display, ctrl, text): 2nd line */
#define FW_SET_COLORS       0x60101DA5u     /* disp_ctrl_set_colors(display, ctrl, colors) */
#define FW_SET_DATA         0x60101DF9u     /* disp_ctrl_set_data(display, ctrl, data) */
#define FW_CTRL_REFRESH     0x60101E85u     /* disp_ctrl_refresh(display, ctrl) */
#define FW_DISP_SYNC        0x60102015u     /* disp_synchronize_q(display) */
#define FIRST_FREE          2u              /* page 3's first unused button */
#define BUTTON2_TEXT_CALL   0x600E5884u     /* page 3: "bl disp_ctrl_set_text" for button 2 (humanize's) */
#define BUTTON3_TEXT_CALL   0x600E5890u     /* ... for button 3 (copypattern's) */
#define STOCK_SET_TEXT      0x60101D5Cu     /* where they go unless a patch hooked them */
#define LAST_BUTTON         6u              /* buttons 2..6 are free in stock; 7 is More */

/* the Pattern grid control: its row texts are 15 rows of 0x13 bytes per track (note +0,
 * instrument +5, FX1 +9, FX2 +0xe); row r shows step centre - 7 + r */
#define GRID_COLORS         0x28u           /* color table: column c's normal at +8 + 4c, empty 0x10 further */
#define GRID_DATA           0x2Cu           /* the row texts (patview +0x14) */
#define GRID_COLOR          0x44u           /* the color of the cell being drawn */
#define GRID_ROWS           15u
#define GRID_CENTRE_ROW     7
#define ROW_SIZE            0x13u
#define TRACK_ROWS          0x11Du
#define COLOR_NORMAL        0x08u
#define COLOR_EMPTY         0x10u           /* + the normal color's offset */
#define FW_SETTINGS         0x2002061Cu
#define SET_ROW_WRAP        0xD2u           /* 0: rows outside the pattern show it wrapped round */


typedef void *(*copy_fn)(void *dst, const void *src, uint32_t size);
typedef uint32_t (*distance_fn)(void *seq, uint32_t track, uint32_t step);
typedef void (*void_fn)(void);
typedef void (*state_fn)(uint32_t state);
typedef void (*bind_fn)(void *manager, uint32_t index, uint32_t flag, void_fn handler);
typedef void (*bind3_fn)(void *manager, uint32_t index, state_fn handler);
typedef void (*ctrl_fn)(void *display, int ctrl, const void *arg);
typedef void (*display_fn)(void *display, int ctrl);
typedef void (*sync_fn)(void *display);
typedef void (*jog_fn)(int32_t delta, uint32_t a1, uint32_t a2, uint32_t a3);
typedef uint32_t (*guard_fn)(uint32_t arg);
typedef void (*cell_fn)(void *ctrl, uint32_t x, uint32_t y, uint32_t font, const char *text,
                        uint32_t n, uint32_t w);

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_pm_next_step_copy);           /* memcpy_small, ITCM thunk 0x1f515 */
CHAIN_WORD(mt_pm_next_roll_a);              /* seq_distance_to_next_note_q, ITCM 0x3959 */
CHAIN_WORD(mt_pm_next_roll_b);              /* ... */
CHAIN_WORD(mt_pm_next_bind);                /* btn_bind 0x600cf6ed (or copypattern's hook) */
CHAIN_WORD(mt_pm_next_label);               /* disp_ctrl_set_text 0x60101d5d */
CHAIN_WORD(mt_pm_next_label2);              /* disp_ctrl_set_ptr38 0x60101d75 */
CHAIN_WORD(mt_pm_next_colors);              /* disp_ctrl_set_colors 0x60101da5 */
CHAIN_WORD(mt_pm_next_data);                /* disp_ctrl_set_data 0x60101df9 */
CHAIN_WORD(mt_pm_next_jog);                 /* pat_jog 0x600c90a1 */
CHAIN_WORD(mt_pm_next_cell_note);           /* patgrid_draw_cell 0x6010b205 */
CHAIN_WORD(mt_pm_next_cell_instr3);         /* ... */
CHAIN_WORD(mt_pm_next_cell_instr);
CHAIN_WORD(mt_pm_next_cell_fx1);
CHAIN_WORD(mt_pm_next_cell_fx2);

/* ---- state (.mt_ram: neither loaded nor zeroed; the patcher places it) ----------- */

#define PM_MAGIC            0x314D504Du     /* "MPM1" */

struct pm_state {
    uint32_t magic;
    uint8_t len[PATTERNS][TRACKS];          /* 0 = the pattern's length */
    uint8_t held;                           /* Trk Len is held */
    char label[4];                          /* its second line while held: "1".."128", "--" */
};
static struct pm_state pm_state_ram __attribute__((section(".mt_ram")));
#define PM (&pm_state_ram)

/* RAM outside .bss holds garbage at power-on. The table is zeroed before the magic
 * word says it is valid, so the audio interrupt never reads a half-made table. */
static void pm_init_if_needed(void)
{
    if (PM->magic == PM_MAGIC)
        return;
    uint8_t *len = &PM->len[0][0];
    for (uint32_t i = 0; i < sizeof PM->len; i++)
        len[i] = 0;
    __asm__ volatile("" ::: "memory");
    PM->magic = PM_MAGIC;
}

/* E(t): the length track t plays, given the pattern's length. Inlined: it runs for
 * every track on every step. */
static inline __attribute__((always_inline)) uint32_t pm_length(uint32_t track, uint32_t pattern_len)
{
    if (PM->magic != PM_MAGIC || track >= TRACKS)
        return pattern_len;
    uint32_t len = PM->len[*FW_PROJECT_PATTERN][track];
    return len >= 1 && len < pattern_len ? len : pattern_len;
}

static const uint8_t *pm_pattern(const uint8_t *seq)
{
    return *(const uint8_t *const *)(seq + SEQ_PATTERN);
}

/* ITCM 0x2170, through a stub that passes r4 (seq) and r5 (track): the copy of the
 * track's step into its work record. src is the step at the track's position; a short
 * track reads the step at its position mod E instead. */
void *mt_pm_step_copy(void *dst, const uint8_t *src, const uint8_t *seq, uint32_t track)
{
    uint32_t pattern_len = pm_pattern(seq)[PAT_LAST_STEP] + 1u;
    uint32_t e = pm_length(track, pattern_len);
    if (e < pattern_len) {
        uint32_t step = *(const uint16_t *)(seq + SEQ_TRACK(track) + TRK_STEP);
        src -= STEP_SIZE * (step - step % e);
    }
    return ((copy_fn)mt_pm_next_step_copy)(dst, src, STEP_SIZE);
}

/* Roll's look-ahead: how many steps from `step` to the next step with a note (1 = the
 * next one), or to the pattern's end when none is left, as stock counts it; a short
 * track's steps are read mod E. */
static uint32_t pm_distance(const uint8_t *seq, uint32_t track, uint32_t step, uint32_t next)
{
    const uint8_t *pattern = pm_pattern(seq);
    uint32_t last = pattern[PAT_LAST_STEP];
    uint32_t e = pm_length(track, last + 1u);
    if (e > last)
        return ((distance_fn)next)((void *)seq, track, step);
    const int8_t *notes = (const int8_t *)(pattern + PAT_STEPS(track));
    uint32_t d = 1;
    for (uint32_t pos = (step + 1u) & 0xFFu; pos <= last; pos++, d++)
        if (notes[STEP_SIZE * (pos % e)] != NOTE_EMPTY)
            return d;
    return d;
}

/* ITCM 0x33a2 and 0x3520: bl seq_distance_to_next_note_q(seq, track, step). */
uint32_t mt_pm_roll_a(const uint8_t *seq, uint32_t track, uint32_t step)
{
    return pm_distance(seq, track, step, mt_pm_next_roll_a);
}

uint32_t mt_pm_roll_b(const uint8_t *seq, uint32_t track, uint32_t step)
{
    return pm_distance(seq, track, step, mt_pm_next_roll_b);
}

/* ---- page 3: the Trk Len button and the encoder ------------------------------------ */

static uint8_t *ed(void)
{
    return (uint8_t *)FW_ED;
}

static int button_ctrl(uint32_t button)
{
    return *(const volatile int8_t *)(ed() + ED_BUTTON_CTRL + button);
}

/* Where a Thumb-2 BL in flash goes */
static uint32_t bl_target(uint32_t at)
{
    uint32_t hw1 = *(const volatile uint16_t *)at, hw2 = *(const volatile uint16_t *)(at + 2);
    uint32_t s = hw1 >> 10 & 1, j1 = hw2 >> 13 & 1, j2 = hw2 >> 11 & 1;
    uint32_t off = s << 24 | (~(j1 ^ s) & 1) << 23 | (~(j2 ^ s) & 1) << 22
                   | (hw1 & 0x3FF) << 12 | (hw2 & 0x7FF) << 1;
    if (s)
        off |= 0xFE000000u;                 /* sign-extend the 25-bit offset */
    return at + 4 + off;
}

/* Trk Len's button: the first one the other page-3 patches leave free. Humanize takes
 * button 2 (it hooks button 2's text call); copypattern takes the first free one after
 * it (it hooks button 3's text call, whichever button it labels). So 2, 3 or 4. The
 * flash doesn't change, so every call gives the same answer, in any bundle order. */
static uint32_t pm_button(void)
{
    return FIRST_FREE + (bl_target(BUTTON2_TEXT_CALL) != STOCK_SET_TEXT)
                      + (bl_target(BUTTON3_TEXT_CALL) != STOCK_SET_TEXT);
}

/* The pattern being edited: its buffer is the sequencer's (pat_change_length edits the
 * same one), its lengths the table's row for project +0x6c8 */
static uint32_t edit_pattern_len(void)
{
    return pm_pattern((const uint8_t *)FW_SEQ)[PAT_LAST_STEP] + 1u;
}

/* The second line: while held, the cursor's track's length, or "--" when it plays the
 * whole pattern; otherwise stock's empty text */
static void pm_show_label(void)
{
    int ctrl = button_ctrl(pm_button());
    if (ctrl < 0)
        return;
    const char *text = FW_EMPTY_TEXT;
    if (PM->held) {
        uint32_t len = edit_pattern_len();
        uint32_t e = pm_length(ed()[ED_CURSOR_TRACK], len);
        char *out = PM->label;
        if (e >= len) {
            *out++ = '-';
            *out++ = '-';
        } else {
            if (e >= 100)
                *out++ = (char)('0' + e / 100);
            if (e >= 10)
                *out++ = (char)('0' + e / 10 % 10);
            *out++ = (char)('0' + e % 10);
        }
        *out = 0;
        text = PM->label;
    }
    ((ctrl_fn)FW_SET_TEXT2)((void *)FW_DISPLAY, ctrl, text);
    ((display_fn)FW_CTRL_REFRESH)((void *)FW_DISPLAY, ctrl);
    ((sync_fn)FW_DISP_SYNC)((void *)FW_DISPLAY);
}

/* Trk Len's button, kind 3: state 1 on press, 0 on release */
void mt_pm_button(uint32_t state)
{
    pm_init_if_needed();
    PM->held = state == 1;
    pm_show_label();
}

/* Trk Len's button is still bound to this patch. The binds of pages 1 and 2 replace or
 * clear it, so a release that went elsewhere (More pressed while holding) can't leave
 * it held. */
static int pm_bound(void)
{
    const uint8_t *manager = *FW_BUTTON_MANAGER;
    uint32_t button = pm_button();
    if (!manager || manager[BTN_COUNT] <= button)
        return 0;
    const uint8_t *entry = *(const uint8_t *const *)(manager + BTN_ENTRIES) + BTN_ENTRY_SIZE * button;
    return entry[0] == BTN_KIND3
        && *(const uint32_t *)(entry + BTN_KIND3_HANDLER) == (uint32_t)mt_pm_button;
}

/* replaces "bl btn_bind" at 0x600e29c6 (page 3: bind button 1 = Track Name); the page
 * has just cleared buttons 0..6 */
void mt_pm_bind(void *manager, uint32_t index, uint32_t flag, void_fn handler)
{
    ((bind_fn)mt_pm_next_bind)(manager, index, flag, handler);
    PM->held = 0;
    uint32_t button = pm_button();
    if (button <= LAST_BUTTON)
        ((bind3_fn)FW_BIND_KIND3)(manager, button, mt_pm_button);
}

/* replaces "bl disp_ctrl_set_text" at 0x600e58b4 (page 3: button 6's empty text, the last
 * of buttons 2..6). Button 6 keeps it; Trk Len's button gets its text after it. */
void mt_pm_label(void *display, int ctrl, const void *text)
{
    ((ctrl_fn)mt_pm_next_label)(display, ctrl, text);
    ((ctrl_fn)FW_SET_TEXT)(display, button_ctrl(pm_button()), "Trk Len");
}

/* replaces "bl disp_ctrl_set_ptr38" at 0x600e5914 (page 3: button 6's empty second line),
 * the same way: the length while held, else empty */
void mt_pm_label2(void *display, int ctrl, const void *text)
{
    ((ctrl_fn)mt_pm_next_label2)(display, ctrl, text);
    ((ctrl_fn)FW_SET_TEXT2)(display, button_ctrl(pm_button()),
                            PM->held && PM->magic == PM_MAGIC ? PM->label : FW_EMPTY_TEXT);
}

/* replaces "bl disp_ctrl_set_colors" at 0x600e594a (page 3: More's colors) */
void mt_pm_colors(void *display, int ctrl, const void *colors)
{
    ((ctrl_fn)mt_pm_next_colors)(display, ctrl, colors);
    const uint8_t *styles = *(const uint8_t *const *)((const uint8_t *)display + DISP_STYLES);
    ((ctrl_fn)FW_SET_COLORS)(display, button_ctrl(pm_button()), styles + STYLE_BUTTON);
}

/* replaces "bl disp_ctrl_set_data" at 0x600e5976 (page 3: More's data) */
void mt_pm_data(void *display, int ctrl, const void *data)
{
    ((ctrl_fn)mt_pm_next_data)(display, ctrl, data);
    ((ctrl_fn)FW_SET_DATA)(display, button_ctrl(pm_button()), (const void *)(FW_PATVIEW + PATVIEW_NAME_DATA));
}

/* Pattern screen encoder word 0x600df2f0 (pat_jog, r0 = the signed delta). While Trk
 * Len is held: the cursor's track's length + delta, clamped to 1..the pattern's length;
 * the pattern's length itself is stored as 0, "follows the pattern". Stock's own guards
 * (an overlay, the check at its start) leave the encoder to stock, which then does
 * nothing. */
void mt_pm_jog(int32_t delta, uint32_t a1, uint32_t a2, uint32_t a3)
{
    if (PM->held && PM->magic == PM_MAGIC) {
        if (!pm_bound()) {
            PM->held = 0;
        } else if (!ed()[ED_OVERLAY] && !((guard_fn)FW_JOG_GUARD)(FW_JOG_GUARD_ARG)) {
            uint32_t track = ed()[ED_CURSOR_TRACK];
            if (track < TRACKS) {
                int32_t len = (int32_t)edit_pattern_len();
                int32_t e = (int32_t)pm_length(track, (uint32_t)len) + delta;
                if (e < 1)
                    e = 1;
                if (e > len)
                    e = len;
                PM->len[*FW_PROJECT_PATTERN][track] = (uint8_t)(e < len ? e : 0);
                int grid = *(const volatile int8_t *)(ed() + ED_GRID_CTRL);
                if (grid >= 0)
                    ((display_fn)FW_CTRL_REFRESH)((void *)FW_DISPLAY, grid);
                pm_show_label();
            }
            return;
        }
    }
    ((jog_fn)mt_pm_next_jog)(delta, a1, a2, a3);
}

/* ---- the Pattern grid ------------------------------------------------------------- */

/* The cell whose text is `text`: if its step is at or past its track's length and it has
 * its column's normal color, the column's empty color; else 0 */
static uint32_t pm_dim_color(const uint8_t *ctrl, const char *text)
{
    if (PM->magic != PM_MAGIC)
        return 0;
    uint32_t off = (uint32_t)((const uint8_t *)text - *(const uint8_t *const *)(ctrl + GRID_DATA));
    if (off >= TRACKS * TRACK_ROWS)
        return 0;
    uint32_t track = off / TRACK_ROWS, row = off % TRACK_ROWS / ROW_SIZE, column;
    switch (off % TRACK_ROWS % ROW_SIZE) {
    case 0x0: column = 0; break;
    case 0x5: column = 1; break;
    case 0x9: column = 2; break;
    case 0xE: column = 3; break;
    default: return 0;
    }
    int32_t len = *(const uint16_t *)(ed() + ED_GRID_LENGTH);
    if (row >= GRID_ROWS || len < 1)
        return 0;
    uint32_t e = pm_length(track, (uint32_t)len);
    if (e >= (uint32_t)len)
        return 0;
    int32_t step = *(const uint16_t *)(ed() + ED_GRID_CENTRE) - GRID_CENTRE_ROW + (int32_t)row;
    if (step < 0 || step >= len) {
        if (*(const uint8_t *)(FW_SETTINGS + SET_ROW_WRAP))
            return 0;                       /* a blank row */
        step %= len;
        if (step < 0)
            step += len;
    }
    if ((uint32_t)step < e)
        return 0;
    const uint32_t *normal = *(const uint32_t *const *)(ctrl + GRID_COLORS) + (COLOR_NORMAL / 4 + column);
    if (*(const uint32_t *)(ctrl + GRID_COLOR) != *normal)
        return 0;
    return normal[COLOR_EMPTY / 4];
}

static void pm_cell(uint32_t next, uint8_t *ctrl, uint32_t x, uint32_t y, uint32_t font,
                    const char *text, uint32_t n, uint32_t w)
{
    uint32_t *color = (uint32_t *)(ctrl + GRID_COLOR);
    uint32_t keep = *color, dim = pm_dim_color(ctrl, text);
    if (dim)
        *color = dim;
    ((cell_fn)next)(ctrl, x, y, font, text, n, w);
    *color = keep;
}

/* replace "bl patgrid_draw_cell(ctrl, x, y, font, text, n, w)" at 0x6010b3de (note),
 * 0x6010b5ae (instrument, 3 characters), 0x6010b62c (instrument), 0x6010b7be (FX1),
 * 0x6010b9c4 (FX2) */
void mt_pm_cell_note(uint8_t *ctrl, uint32_t x, uint32_t y, uint32_t font, const char *text, uint32_t n, uint32_t w)
{
    pm_cell(mt_pm_next_cell_note, ctrl, x, y, font, text, n, w);
}

void mt_pm_cell_instr3(uint8_t *ctrl, uint32_t x, uint32_t y, uint32_t font, const char *text, uint32_t n, uint32_t w)
{
    pm_cell(mt_pm_next_cell_instr3, ctrl, x, y, font, text, n, w);
}

void mt_pm_cell_instr(uint8_t *ctrl, uint32_t x, uint32_t y, uint32_t font, const char *text, uint32_t n, uint32_t w)
{
    pm_cell(mt_pm_next_cell_instr, ctrl, x, y, font, text, n, w);
}

void mt_pm_cell_fx1(uint8_t *ctrl, uint32_t x, uint32_t y, uint32_t font, const char *text, uint32_t n, uint32_t w)
{
    pm_cell(mt_pm_next_cell_fx1, ctrl, x, y, font, text, n, w);
}

void mt_pm_cell_fx2(uint8_t *ctrl, uint32_t x, uint32_t y, uint32_t font, const char *text, uint32_t n, uint32_t w)
{
    pm_cell(mt_pm_next_cell_fx2, ctrl, x, y, font, text, n, w);
}

/* Config's version text: 1.2.0-pm1 */
const char mt_version_fmt[] = "%d.%d.%d-" POLYMETER_VERSION;
