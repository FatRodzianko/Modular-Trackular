/*
 * tracklen, Variable Track Length: a length per track (Polyend Tracker+ firmware 1.2.0).
 *
 * The idea comes from a custom firmware for the Tracker Mini shown on r/PolyendTracker
 * (each track has its own length; steps past it are dimmed; all tracks line up again at
 * the end of the pattern). This is a re-implementation from that description; nothing
 * of it was seen or used. docs/TRACK_LENGTH_PLAN.md has the investigation and the plan.
 *
 * A track with length E shorter than the pattern plays its steps 0..E-1 over and over,
 * and starts again with every other track when the pattern ends: in a 16-step pattern a
 * track of length 6 plays 0-5, 0-5, 0-3. The sequencer itself is left alone. Every
 * track's position (+0x88) still counts the whole pattern, so the pattern end, song
 * mode's next-pattern preload, the metronome and the Pattern screen's play row (which
 * all read track 1's position) behave as in stock firmware. Only the reads of a track's
 * step data change:
 *  - seq_process_track (ITCM) copies a step's 6 bytes into the track's work record at
 *    micro-step 1 with memcpy_small at ITCM 0x2170. mt_tl_step_copy reads step
 *    (position mod E) instead. Every play mode goes through it: pattern loop, song mode,
 *    Play from a position, a looping selection, performance mode;
 *  - Roll looks ahead for the next note (seq_distance_to_next_note_q, ITCM 0x3958,
 *    called at 0x33a2 and 0x3520) to know how long the roll lasts. mt_tl_roll_a/_b look
 *    at the same steps mod E, up to the pattern's end as stock does.
 *
 * E(t) is the track's length from the table of the playing pattern (project +0x6c8)
 * when it is 1..pattern length - 1; anything else (0 = none, or a length the pattern
 * has been shortened below) is the pattern's length, which is exactly stock. The table
 * is read in the audio interrupt; until it is set up (the magic word) nothing changes.
 *
 * Setting a length: the Pattern screen's page 3 gets a "Trk Len" button, on the first
 * button after the other patches' (tl_button: 2, or 3 or 4 after Humanize and Copy to
 * New). While it is held, the encoder changes the cursor's track's length, 1..the
 * pattern's length. The button's second line always shows the cursor's track's length
 * ("--" when the track follows the pattern); the Pattern screen's update redraws it when
 * the track, the pattern or the length changes. Released, the encoder is stock's again.
 *  - mt_tl_bind chains page 3's bind of button 1 (0x600e29c6, shared with copypattern)
 *    and binds Trk Len's button as kind 3 (called on press and on release);
 *  - mt_tl_label / mt_tl_label2 chain button 6's two (empty) text calls in page 3's
 *    labels (0x600e58b4, 0x600e5914), the last of buttons 2..6, and then give Trk Len's
 *    button its texts, over the empty ones stock has just set;
 *  - mt_tl_colors / mt_tl_data chain More's colors and data calls (0x600e594a,
 *    0x600e5976), the only ones after the texts, and give Trk Len's button the active
 *    colors and Pattern Name's data, as page 3 gives buttons 0 and 1;
 *  - mt_tl_jog takes the Pattern screen's encoder word (0x600df2f0, pat_jog 0x600c90a1)
 *    while the button is held and still bound to this patch, and refills and redraws the
 *    grid (stock's row fill, so the steps past the length show the new loop).
 *
 * The Pattern grid shows a short track's steps E.. grey, in a muted track's color, so they
 * read as "nothing plays here" with their contents still visible; empty ones ("-") are
 * blank, so the column stops at E. The grid control picks each cell's color into ctrl
 * +0x44 and draws its text with patgrid_draw_cell (0x6010b204); mt_tl_cell_* chain its 5
 * calls (note, instrument twice, FX1, FX2) and swap the color or the text for the call.
 * Only the column's normal and empty colors are swapped, so muted tracks, the cursor and
 * a selection look as in stock.
 *
 * Live recording during play writes a note at the track's position (+0x88), or at the
 * next step when the hit is late, and a note-off one step further when it would land
 * on the note's own step: seq_record_live_note (ITCM 0x15c8, reached only through 8
 * flash veneers) and seq_record_note_off_q (0x600ac700, 16 calls and 2 tail calls).
 * Both pick the row inline, so mt_tl_rec_note* / mt_tl_rec_off* wrap the whole call:
 * each short track's rows position .. position + 3 that lie past E get the contents of
 * their step mod E (what stock's rules then read); after the call, a row stock changed
 * goes to its step mod E, and the dimmed rows get their own contents back. So a short
 * track records as stock decides, onto the steps it plays. The copying is done with
 * the audio interrupt masked; the call itself runs as in stock (the recorders mask and
 * enable the interrupt themselves). The interrupt can still run during the call:
 * playback reads a short track's steps below E only, so it never sees the borrowed
 * rows; the fourth row covers the position moving on meanwhile; and a recording from
 * the interrupt, nested in this one, puts back exactly what it found.
 * One change from stock's rules, for notes on short tracks: stock records a note only
 * onto a step without one, and a note-off (which it writes after each recorded note when
 * the pad is let go) counts as one. All of a short track's loops share its E steps, so
 * a few notes and their note-offs fill them in the first loop and every later hit went
 * to another track. So for the note recorder, a short track's steps it can reach that
 * hold a note-off are shown to stock as empty; a step stock didn't record on gets its
 * note-off back. Notes are never replaced. (The audio interrupt could play such a step
 * during the call and miss its note-off once: the note then sounds on to the next one.)
 *
 * The lengths are saved per project in <project folder>/tracklen.bin (TLN1: magic, the
 * number of entries, one 17-byte entry per pattern that has a length: its number and
 * the 16 lengths, a checksum), read and written from the Pattern and Config screens'
 * update methods (chained), one file read or write per call, in pieces of at most 256
 * bytes, only while the file manager is idle:
 *  - another project: the lengths are cleared at once, then read from its file (checked
 *    whole before any of it is used);
 *  - a change: written a moment later; with no lengths left, the file is removed;
 *  - Save As (and a new project's first save, which is one): the firmware copies only
 *    its own files to the new folder, so the lengths go with the project to its new name
 *    and are written there. The Save As request's name copy (0x60114eaa) marks it;
 *  - a new project ("New Project", no path) has no folder yet: nothing is written until
 *    its first save.
 * Stock firmware never reads the file: there every track plays the whole pattern.
 *
 * Shared hooks go through chain words (mt_tl_next_*) that tools/patch_ptf.py fills with
 * what the site pointed to before, so other patches can hook them too.
 */
#include <stdint.h>

#ifndef TRACKLEN_VERSION
#define TRACKLEN_VERSION "tl7"
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
#define FW_FILL_ROWS        0x600DF3A1u     /* patview_fill_rows_q(patview): the grid's row texts */
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
#define COLOR_MUTED         0x30u           /* a muted track's cells */
#define FW_SETTINGS         0x2002061Cu
#define SET_ROW_WRAP        0xD2u           /* 0: rows outside the pattern show it wrapped round */

/* live recording */
#define REC_ROWS            4u              /* the rows a recorder writes: position + 0..2, + 1 if it moves on */
#define NOTE_OFF            0xFCu           /* a step's note byte: note-off (no note: 0xFF) */
#define FW_NVIC_ISER0       ((volatile uint32_t *)0xE000E100u)
#define FW_NVIC_ICER0       ((volatile uint32_t *)0xE000E180u)
#define AUDIO_IRQ           1u              /* NVIC bit 0: the audio block (audio_irq_disable 0x600303d4) */

/* the file manager and the project */
#define FW_FM               0x2001E488u     /* file manager object; *FW_FM = FS object */
#define FM_STATE            0x13Du          /* file manager state; 0 or 0x0c when idle */
#define FM_SUBSTATE         0x13Fu
#define FW_PROJECT_PATH     ((const uint8_t *)(FW_FM + 0x241u))     /* 254 characters and a 0 */
#define FW_PROJECT_NAME     ((const uint8_t *)(FW_FM + 0x340u))     /* 32 characters and a 0 */
#define PATH_MAX_LEN        254u
#define NAME_MAX_LEN        32u
#define FW_SNPRINTF         0x6016712Du
#define SAVE_DELAY          25u             /* screen updates between a change and the save */

typedef void *(*copy_fn)(void *dst, const void *src, uint32_t size);
typedef uint32_t (*distance_fn)(void *seq, uint32_t track, uint32_t step);
typedef void (*void_fn)(void);
typedef void (*state_fn)(uint32_t state);
typedef void (*bind_fn)(void *manager, uint32_t index, uint32_t flag, void_fn handler);
typedef void (*bind3_fn)(void *manager, uint32_t index, state_fn handler);
typedef void (*ctrl_fn)(void *display, int ctrl, const void *arg);
typedef void (*display_fn)(void *display, int ctrl);
typedef void (*sync_fn)(void *display);
typedef void (*fill_fn)(void *patview);
typedef void (*jog_fn)(int32_t delta, uint32_t a1, uint32_t a2, uint32_t a3);
typedef uint32_t (*guard_fn)(uint32_t arg);
typedef void (*cell_fn)(void *ctrl, uint32_t x, uint32_t y, uint32_t font, const char *text,
                        uint32_t n, uint32_t w);
typedef uint32_t (*rec_fn)(void *seq, uint32_t source, uint32_t note, uint32_t velocity, uint32_t arg);
typedef void (*update_fn)(void *screen, void *a1, void *a2, void *a3);
typedef int (*snprintf_fn)(char *buf, uint32_t size, const char *fmt, ...);
typedef char *(*strncpy_fn)(char *dst, const char *src, uint32_t n);

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_tl_next_step_copy);           /* memcpy_small, ITCM thunk 0x1f515 */
CHAIN_WORD(mt_tl_next_roll_a);              /* seq_distance_to_next_note_q, ITCM 0x3959 */
CHAIN_WORD(mt_tl_next_roll_b);              /* ... */
CHAIN_WORD(mt_tl_next_bind);                /* btn_bind 0x600cf6ed (or copypattern's hook) */
CHAIN_WORD(mt_tl_next_label);               /* disp_ctrl_set_text 0x60101d5d */
CHAIN_WORD(mt_tl_next_label2);              /* disp_ctrl_set_ptr38 0x60101d75 */
CHAIN_WORD(mt_tl_next_colors);              /* disp_ctrl_set_colors 0x60101da5 */
CHAIN_WORD(mt_tl_next_data);                /* disp_ctrl_set_data 0x60101df9 */
CHAIN_WORD(mt_tl_next_jog);                 /* pat_jog 0x600c90a1 */
CHAIN_WORD(mt_tl_next_cell_note);           /* patgrid_draw_cell 0x6010b205 */
CHAIN_WORD(mt_tl_next_cell_instr3);         /* ... */
CHAIN_WORD(mt_tl_next_cell_instr);
CHAIN_WORD(mt_tl_next_cell_fx1);
CHAIN_WORD(mt_tl_next_cell_fx2);
CHAIN_WORD(mt_tl_next_pattern_update);      /* Pattern screen update 0x600e1f99 (or a patch's) */
CHAIN_WORD(mt_tl_next_config_update);       /* Config screen update 0x600c6475 (or a patch's) */
CHAIN_WORD(mt_tl_next_save_name);           /* strncpy, in the save request (or a patch's) */

/* ---- state (.mt_ram: neither loaded nor zeroed; the patcher places it) ----------- */

#define TL_MAGIC            0x314C544Du     /* "MTL1" */

struct tl_state {
    uint32_t magic;
    uint8_t len[PATTERNS][TRACKS];          /* 0 = the pattern's length */
    uint32_t session;                       /* project name + path the lengths belong to; 0: none yet */
    uint32_t save_as;                       /* the session a Save As moves the lengths to; 0: none */
    uint16_t wait;                          /* screen updates until the next save */
    uint8_t load;                           /* read the project's file once the file manager is idle */
    uint8_t changed;                        /* a length changed since the file was read or written */
    uint8_t held;                           /* Trk Len is held */
    char label[4];                          /* its second line: "1".."128", "--" */
    uint8_t shown[3];                       /* what it shows: the track, the pattern, E (0: "--") */
};
static struct tl_state tl_state_ram __attribute__((section(".mt_ram")));
#define TL (&tl_state_ram)

/* RAM outside .bss holds garbage at power-on. The table is zeroed before the magic
 * word says it is valid, so the audio interrupt never reads a half-made table. */
static void tl_init_if_needed(void)
{
    if (TL->magic == TL_MAGIC)
        return;
    uint8_t *len = &TL->len[0][0];
    for (uint32_t i = 0; i < sizeof TL->len; i++)
        len[i] = 0;
    TL->session = TL->save_as = 0;
    TL->wait = SAVE_DELAY;
    TL->load = TL->changed = TL->held = 0;
    __asm__ volatile("" ::: "memory");
    TL->magic = TL_MAGIC;
}

/* E(t): the length track t plays, given the pattern's length. Inlined: it runs for
 * every track on every step. */
static inline __attribute__((always_inline)) uint32_t tl_length(uint32_t track, uint32_t pattern_len)
{
    if (TL->magic != TL_MAGIC || track >= TRACKS)
        return pattern_len;
    uint32_t len = TL->len[*FW_PROJECT_PATTERN][track];
    return len >= 1 && len < pattern_len ? len : pattern_len;
}

static const uint8_t *tl_pattern(const uint8_t *seq)
{
    return *(const uint8_t *const *)(seq + SEQ_PATTERN);
}

/* For condtrig, linked to it by the patcher ("links"): the length track t plays in seq's
 * pattern, 0 when it plays the whole pattern. Its conditions count the track's own loops. */
uint32_t mt_tl_len(const uint8_t *seq, uint32_t track)
{
    uint32_t pattern_len = tl_pattern(seq)[PAT_LAST_STEP] + 1u;
    uint32_t e = tl_length(track, pattern_len);
    return e < pattern_len ? e : 0u;
}

/* ITCM 0x2170, through a stub that passes r4 (seq) and r5 (track): the copy of the
 * track's step into its work record. src is the step at the track's position; a short
 * track reads the step at its position mod E instead. */
void *mt_tl_step_copy(void *dst, const uint8_t *src, const uint8_t *seq, uint32_t track)
{
    uint32_t pattern_len = tl_pattern(seq)[PAT_LAST_STEP] + 1u;
    uint32_t e = tl_length(track, pattern_len);
    if (e < pattern_len) {
        uint32_t step = *(const uint16_t *)(seq + SEQ_TRACK(track) + TRK_STEP);
        src -= STEP_SIZE * (step - step % e);
    }
    return ((copy_fn)mt_tl_next_step_copy)(dst, src, STEP_SIZE);
}

/* Roll's look-ahead: how many steps from `step` to the next step with a note (1 = the
 * next one), or to the pattern's end when none is left, as stock counts it; a short
 * track's steps are read mod E. The steps repeat every E, so at most E of them are read,
 * each once, counted round rather than divided per step: E steps without a note means
 * none comes before the pattern's end. */
static uint32_t tl_distance(const uint8_t *seq, uint32_t track, uint32_t step, uint32_t next)
{
    const uint8_t *pattern = tl_pattern(seq);
    uint32_t last = pattern[PAT_LAST_STEP];
    uint32_t e = tl_length(track, last + 1u);
    if (e > last)
        return ((distance_fn)next)((void *)seq, track, step);
    uint32_t pos = (step + 1u) & 0xFFu;
    if (pos > last)
        return 1;
    uint32_t span = last - pos + 1u;        /* the steps up to the pattern's end */
    const int8_t *notes = (const int8_t *)(pattern + PAT_STEPS(track));
    const int8_t *p = notes + STEP_SIZE * (pos % e), *end = notes + STEP_SIZE * e;
    for (uint32_t d = 1; d <= span && d <= e; d++) {
        if (*p != NOTE_EMPTY)
            return d;
        p += STEP_SIZE;
        if (p == end)
            p = notes;
    }
    return span + 1u;
}

/* ITCM 0x33a2 and 0x3520: bl seq_distance_to_next_note_q(seq, track, step). */
uint32_t mt_tl_roll_a(const uint8_t *seq, uint32_t track, uint32_t step)
{
    return tl_distance(seq, track, step, mt_tl_next_roll_a);
}

uint32_t mt_tl_roll_b(const uint8_t *seq, uint32_t track, uint32_t step)
{
    return tl_distance(seq, track, step, mt_tl_next_roll_b);
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
static uint32_t tl_button(void)
{
    return FIRST_FREE + (bl_target(BUTTON2_TEXT_CALL) != STOCK_SET_TEXT)
                      + (bl_target(BUTTON3_TEXT_CALL) != STOCK_SET_TEXT);
}

/* The pattern being edited: its buffer is the sequencer's (pat_change_length edits the
 * same one), its lengths the table's row for project +0x6c8 */
static uint32_t edit_pattern_len(void)
{
    return tl_pattern((const uint8_t *)FW_SEQ)[PAT_LAST_STEP] + 1u;
}

/* What the second line shows: the cursor's track, the pattern, its E (0 when it plays the
 * whole pattern) */
static void label_key(uint8_t key[3])
{
    uint32_t len = edit_pattern_len(), track = ed()[ED_CURSOR_TRACK];
    uint32_t e = tl_length(track, len);
    key[0] = (uint8_t)track;
    key[1] = *FW_PROJECT_PATTERN;
    key[2] = (uint8_t)(e < len ? e : 0);
}

/* The second line, held or not: the cursor's track's length, or "--" when it plays the
 * whole pattern */
static const char *tl_format_label(void)
{
    label_key(TL->shown);
    uint32_t e = TL->shown[2];
    char *out = TL->label;
    if (!e) {
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
    return TL->label;
}

/* The grid's row texts written again (as stock's encoder does after an edit), so the
 * steps past a length show the loop for the new length; the fill ends by redrawing the
 * grid control itself */
static void tl_refill_grid(void)
{
    if (*(const volatile int8_t *)(ed() + ED_GRID_CTRL) >= 0)
        ((fill_fn)FW_FILL_ROWS)((void *)FW_PATVIEW);
}

static void tl_show_label(void)
{
    int ctrl = button_ctrl(tl_button());
    if (ctrl < 0)
        return;
    ((ctrl_fn)FW_SET_TEXT2)((void *)FW_DISPLAY, ctrl, tl_format_label());
    ((display_fn)FW_CTRL_REFRESH)((void *)FW_DISPLAY, ctrl);
    ((sync_fn)FW_DISP_SYNC)((void *)FW_DISPLAY);
}

/* Trk Len's button, kind 3: state 1 on press, 0 on release. A button still held a moment
 * later sends 2 (the input callback 0x600cc610 passes it on to kind 3); like stock's
 * Length (0x600e249c), only 1 and 0 count. */
void mt_tl_button(uint32_t state)
{
    if (state > 1)
        return;
    tl_init_if_needed();
    TL->held = state == 1;
    tl_show_label();
}

/* Trk Len's button is still bound to this patch. The binds of pages 1 and 2 replace or
 * clear it, so a release that went elsewhere (More pressed while holding) can't leave
 * it held. */
static int tl_bound(void)
{
    const uint8_t *manager = *FW_BUTTON_MANAGER;
    uint32_t button = tl_button();
    if (!manager || manager[BTN_COUNT] <= button)
        return 0;
    const uint8_t *entry = *(const uint8_t *const *)(manager + BTN_ENTRIES) + BTN_ENTRY_SIZE * button;
    return entry[0] == BTN_KIND3
        && *(const uint32_t *)(entry + BTN_KIND3_HANDLER) == (uint32_t)mt_tl_button;
}

/* replaces "bl btn_bind" at 0x600e29c6 (page 3: bind button 1 = Track Name); the page
 * has just cleared buttons 0..6 */
void mt_tl_bind(void *manager, uint32_t index, uint32_t flag, void_fn handler)
{
    ((bind_fn)mt_tl_next_bind)(manager, index, flag, handler);
    TL->held = 0;
    uint32_t button = tl_button();
    if (button <= LAST_BUTTON)
        ((bind3_fn)FW_BIND_KIND3)(manager, button, mt_tl_button);
}

/* replaces "bl disp_ctrl_set_text" at 0x600e58b4 (page 3: button 6's empty text, the last
 * of buttons 2..6). Button 6 keeps it; Trk Len's button gets its text after it. */
void mt_tl_label(void *display, int ctrl, const void *text)
{
    ((ctrl_fn)mt_tl_next_label)(display, ctrl, text);
    ((ctrl_fn)FW_SET_TEXT)(display, button_ctrl(tl_button()), "Trk Len");
}

/* replaces "bl disp_ctrl_set_ptr38" at 0x600e5914 (page 3: button 6's empty second line),
 * the same way: the cursor's track's length */
void mt_tl_label2(void *display, int ctrl, const void *text)
{
    ((ctrl_fn)mt_tl_next_label2)(display, ctrl, text);
    tl_init_if_needed();
    ((ctrl_fn)FW_SET_TEXT2)(display, button_ctrl(tl_button()), tl_format_label());
}

/* replaces "bl disp_ctrl_set_colors" at 0x600e594a (page 3: More's colors) */
void mt_tl_colors(void *display, int ctrl, const void *colors)
{
    ((ctrl_fn)mt_tl_next_colors)(display, ctrl, colors);
    const uint8_t *styles = *(const uint8_t *const *)((const uint8_t *)display + DISP_STYLES);
    ((ctrl_fn)FW_SET_COLORS)(display, button_ctrl(tl_button()), styles + STYLE_BUTTON);
}

/* replaces "bl disp_ctrl_set_data" at 0x600e5976 (page 3: More's data) */
void mt_tl_data(void *display, int ctrl, const void *data)
{
    ((ctrl_fn)mt_tl_next_data)(display, ctrl, data);
    ((ctrl_fn)FW_SET_DATA)(display, button_ctrl(tl_button()), (const void *)(FW_PATVIEW + PATVIEW_NAME_DATA));
}

/* Pattern screen encoder word 0x600df2f0 (pat_jog, r0 = the signed delta). While Trk
 * Len is held: the cursor's track's length + delta, clamped to 1..the pattern's length;
 * the pattern's length itself is stored as 0, "follows the pattern". Stock's own guards
 * (an overlay, the check at its start) leave the encoder to stock, which then does
 * nothing. */
void mt_tl_jog(int32_t delta, uint32_t a1, uint32_t a2, uint32_t a3)
{
    if (TL->held && TL->magic == TL_MAGIC) {
        if (!tl_bound()) {
            TL->held = 0;
        } else if (!ed()[ED_OVERLAY] && !((guard_fn)FW_JOG_GUARD)(FW_JOG_GUARD_ARG)) {
            uint32_t track = ed()[ED_CURSOR_TRACK];
            if (track < TRACKS) {
                int32_t len = (int32_t)edit_pattern_len();
                int32_t e = (int32_t)tl_length(track, (uint32_t)len) + delta;
                if (e < 1)
                    e = 1;
                if (e > len)
                    e = len;
                uint8_t *slot = &TL->len[*FW_PROJECT_PATTERN][track], was = *slot;
                *slot = (uint8_t)(e < len ? e : 0);
                TL->changed |= *slot != was;
                tl_refill_grid();
                tl_show_label();
            }
            return;
        }
    }
    ((jog_fn)mt_tl_next_jog)(delta, a1, a2, a3);
}

/* ---- the Pattern grid ------------------------------------------------------------- */

/* The cell whose text is `text`: if its step is at or past its track's length, the grid's
 * color table, and its column in *column; else 0 */
static const uint32_t *tl_past_end(const uint8_t *ctrl, const char *text, uint32_t *column)
{
    if (TL->magic != TL_MAGIC)
        return 0;
    uint32_t off = (uint32_t)((const uint8_t *)text - *(const uint8_t *const *)(ctrl + GRID_DATA));
    if (off >= TRACKS * TRACK_ROWS)
        return 0;
    uint32_t track = off / TRACK_ROWS, row = off % TRACK_ROWS / ROW_SIZE;
    switch (off % TRACK_ROWS % ROW_SIZE) {
    case 0x0: *column = 0; break;
    case 0x5: *column = 1; break;
    case 0x9: *column = 2; break;
    case 0xE: *column = 3; break;
    default: return 0;
    }
    int32_t len = *(const uint16_t *)(ed() + ED_GRID_LENGTH);
    if (row >= GRID_ROWS || len < 1)
        return 0;
    uint32_t e = tl_length(track, (uint32_t)len);
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
    return *(const uint32_t *const *)(ctrl + GRID_COLORS);
}

/* The step a grid row of track `track` shows its texts from: past a short track's length,
 * the step it plays there (step mod E), so the grid shows the loop going on, grey */
uint32_t mt_tl_ghost_step(int32_t step, uint32_t track)
{
    int32_t len = *(const int16_t *)(ed() + ED_GRID_LENGTH);
    if (TL->magic != TL_MAGIC || track >= TRACKS || len < 1 || step < 0 || step >= len)
        return (uint32_t)step;
    uint32_t e = tl_length(track, (uint32_t)len);
    return (uint32_t)step >= e ? (uint32_t)step % e : (uint32_t)step;
}

/* 0x600df548 in patview_fill_rows_q (0x600df3a0), over "add.w r3, r6, r6, lsl #1"
 * ("replaces": "insns"): every row's three ways to its step (in the pattern, wrapped below
 * 0, wrapped past the end) meet here with the step in r6 and the track in fp, and every
 * read of the step's note, instrument and FX after it goes through r6. Sets r6 to the
 * step to show and r3 as that instruction does; keeps every other register (r2 is live).
 * The flags are dead: the code after it sets them afresh. */
__attribute__((naked)) void mt_tl_ghost(void)
{
    __asm__ volatile(
        "push {r0, r1, r2, r3, ip, lr}\n\t"
        "mov r0, r6\n\t"
        "mov r1, fp\n\t"
        "bl mt_tl_ghost_step\n\t"
        "mov r6, r0\n\t"
        "add.w r3, r6, r6, lsl #1\n\t"
        "pop {r0, r1, r2}\n\t"
        "add sp, sp, #4\n\t"
        "pop {ip, pc}\n\t");
}

/* Past the end, a cell in its column's normal color is drawn grey, in a muted track's
 * color, and an empty one ("-", in the empty color) as blank, as many spaces as it has
 * characters, so the track visibly stops at its length. The cursor, a selection and muted
 * tracks keep their own colors and texts. */
static void tl_cell(uint32_t next, uint8_t *ctrl, uint32_t x, uint32_t y, uint32_t font,
                    const char *text, uint32_t n, uint32_t w)
{
    static const char spaces[] = "     ";
    uint32_t *color = (uint32_t *)(ctrl + GRID_COLOR);
    uint32_t keep = *color;
    uint32_t column = 0;
    const uint32_t *colors = tl_past_end(ctrl, text, &column);
    if (colors) {
        const uint32_t *normal = colors + COLOR_NORMAL / 4 + column;
        if (keep == normal[0]) {
            *color = colors[COLOR_MUTED / 4];
        } else if (keep == normal[COLOR_EMPTY / 4] && text[0] == '-') {
            uint32_t k = 0;
            while (k < sizeof spaces - 1 && text[k])
                k++;
            text = spaces + (sizeof spaces - 1 - k);
        }
    }
    ((cell_fn)next)(ctrl, x, y, font, text, n, w);
    *color = keep;
}

/* replace "bl patgrid_draw_cell(ctrl, x, y, font, text, n, w)" at 0x6010b3de (note),
 * 0x6010b5ae (instrument, 3 characters), 0x6010b62c (instrument), 0x6010b7be (FX1),
 * 0x6010b9c4 (FX2) */
void mt_tl_cell_note(uint8_t *ctrl, uint32_t x, uint32_t y, uint32_t font, const char *text, uint32_t n, uint32_t w)
{
    tl_cell(mt_tl_next_cell_note, ctrl, x, y, font, text, n, w);
}

void mt_tl_cell_instr3(uint8_t *ctrl, uint32_t x, uint32_t y, uint32_t font, const char *text, uint32_t n, uint32_t w)
{
    tl_cell(mt_tl_next_cell_instr3, ctrl, x, y, font, text, n, w);
}

void mt_tl_cell_instr(uint8_t *ctrl, uint32_t x, uint32_t y, uint32_t font, const char *text, uint32_t n, uint32_t w)
{
    tl_cell(mt_tl_next_cell_instr, ctrl, x, y, font, text, n, w);
}

void mt_tl_cell_fx1(uint8_t *ctrl, uint32_t x, uint32_t y, uint32_t font, const char *text, uint32_t n, uint32_t w)
{
    tl_cell(mt_tl_next_cell_fx1, ctrl, x, y, font, text, n, w);
}

void mt_tl_cell_fx2(uint8_t *ctrl, uint32_t x, uint32_t y, uint32_t font, const char *text, uint32_t n, uint32_t w)
{
    tl_cell(mt_tl_next_cell_fx2, ctrl, x, y, font, text, n, w);
}

/* ---- live recording --------------------------------------------------------------- */

static void copy_step(uint8_t *dst, const uint8_t *src)
{
    for (uint32_t i = 0; i < STEP_SIZE; i++)
        dst[i] = src[i];
}

static int same_step(const uint8_t *a, const uint8_t *b)
{
    for (uint32_t i = 0; i < STEP_SIZE; i++)
        if (a[i] != b[i])
            return 0;
    return 1;
}

/* Mask the audio interrupt; returns whether it was enabled (it isn't inside the
 * interrupt, or while a caller has masked it) */
static uint32_t audio_mask(void)
{
    uint32_t was = *FW_NVIC_ISER0 & AUDIO_IRQ;
    *FW_NVIC_ICER0 = AUDIO_IRQ;
    __asm__ volatile("dsb\n\tisb" ::: "memory");
    return was;
}

static void audio_restore(uint32_t was)
{
    if (was)
        *FW_NVIC_ISER0 = AUDIO_IRQ;
}

/* seq_record_live_note / seq_record_note_off_q(seq, source, note, velocity, a 16-bit
 * stack argument), with the rows a short track's recording can reach mapped onto the
 * steps it plays (see the top of the file) */
static uint32_t tl_record(uint32_t next, uint8_t *seq, uint32_t source, uint32_t note, uint32_t velocity,
                          uint32_t arg, uint32_t lift_offs)
{
    if (TL->magic != TL_MAGIC)
        return ((rec_fn)next)(seq, source, note, velocity, arg);
    uint8_t *pattern = *(uint8_t *const *)(seq + SEQ_PATTERN);
    uint32_t len = pattern[PAT_LAST_STEP] + 1u, tracks = 0;
    for (uint32_t t = 0; t < TRACKS; t++)
        if (tl_length(t, len) < len)
            tracks |= 1u << t;
    if (!tracks)
        return ((rec_fn)next)(seq, source, note, velocity, arg);

    uint8_t own[TRACKS][REC_ROWS][STEP_SIZE];       /* a dimmed row's own contents */
    uint8_t lent[TRACKS][REC_ROWS][STEP_SIZE];      /* what it was given: its step mod E */
    uint8_t row_of[TRACKS][REC_ROWS];
    uint8_t lifted[TRACKS][REC_ROWS];               /* steps below E whose note-off was lifted */
    uint8_t n_lifted[TRACKS];
    uint32_t audio = audio_mask();
    for (uint32_t t = 0; t < TRACKS; t++) {
        if (!(tracks >> t & 1))
            continue;
        uint32_t e = tl_length(t, len);
        uint32_t pos = *(const uint16_t *)(seq + SEQ_TRACK(t) + TRK_STEP);
        uint8_t *steps = pattern + PAT_STEPS(t);
        n_lifted[t] = 0;
        for (uint32_t k = 0; lift_offs && k < REC_ROWS; k++) {
            uint32_t r = (pos + k) % len % e;
            if (steps[STEP_SIZE * r] == NOTE_OFF) {
                steps[STEP_SIZE * r] = 0xFFu;
                lifted[t][n_lifted[t]++] = (uint8_t)r;
            }
        }
        for (uint32_t k = 0; k < REC_ROWS; k++) {
            uint32_t x = (pos + k) % len;
            row_of[t][k] = (uint8_t)x;
            if (x < e)
                continue;
            copy_step(own[t][k], steps + STEP_SIZE * x);
            copy_step(lent[t][k], steps + STEP_SIZE * (x % e));
            copy_step(steps + STEP_SIZE * x, lent[t][k]);
        }
    }
    audio_restore(audio);
    uint32_t ret = ((rec_fn)next)(seq, source, note, velocity, arg);
    audio = audio_mask();
    for (uint32_t t = 0; t < TRACKS; t++) {
        if (!(tracks >> t & 1))
            continue;
        uint32_t e = tl_length(t, len);
        uint8_t *steps = pattern + PAT_STEPS(t);
        for (uint32_t k = 0; k < REC_ROWS; k++) {
            uint32_t x = row_of[t][k];
            if (x < e)
                continue;
            if (!same_step(steps + STEP_SIZE * x, lent[t][k]))
                copy_step(steps + STEP_SIZE * (x % e), steps + STEP_SIZE * x);
            copy_step(steps + STEP_SIZE * x, own[t][k]);
        }
        for (uint32_t i = 0; i < n_lifted[t]; i++)
            if (steps[STEP_SIZE * lifted[t][i]] == 0xFFu)
                steps[STEP_SIZE * lifted[t][i]] = NOTE_OFF;
    }
    audio_restore(audio);
    return ret;
}

/* One hook per site, each with its own chain word: the 8 veneers' literal words for
 * seq_record_live_note (0x15c9), and the 18 calls of seq_record_note_off_q */
#define REC_HOOK(name, lift)                                                                \
    CHAIN_WORD(mt_tl_next_##name);                                                          \
    uint32_t mt_tl_##name(uint8_t *seq, uint32_t source, uint32_t note, uint32_t velocity,  \
                          uint32_t arg)                                                     \
    {                                                                                       \
        return tl_record(mt_tl_next_##name, seq, source, note, velocity, arg, lift);        \
    }
#define REC_NOTE(name) REC_HOOK(name, 1u)    /* notes: a short track's note-offs don't block */
#define REC_OFF(name) REC_HOOK(name, 0u)
REC_NOTE(rec_note1) REC_NOTE(rec_note2) REC_NOTE(rec_note3) REC_NOTE(rec_note4)
REC_NOTE(rec_note5) REC_NOTE(rec_note6) REC_NOTE(rec_note7) REC_NOTE(rec_note8)
REC_OFF(rec_off1) REC_OFF(rec_off2) REC_OFF(rec_off3) REC_OFF(rec_off4) REC_OFF(rec_off5)
REC_OFF(rec_off6) REC_OFF(rec_off7) REC_OFF(rec_off8) REC_OFF(rec_off9) REC_OFF(rec_off10)
REC_OFF(rec_off11) REC_OFF(rec_off12) REC_OFF(rec_off13) REC_OFF(rec_off14) REC_OFF(rec_off15)
REC_OFF(rec_off16) REC_OFF(rec_off17) REC_OFF(rec_off18)

/* ---- tracklen.bin (main loop: the screens' update methods) ------------------------ */

struct fs_open_result { uint32_t handle; uint8_t ok; uint8_t pad[3]; };
typedef void (*fs_open_fn)(struct fs_open_result *res, void *fs, const char *path, uint32_t mode, uint32_t x);
typedef int (*fs_read_fn)(void *fs, void *buf, uint32_t size, uint32_t handle);
typedef int (*fs_write_fn)(void *fs, const void *buf, uint32_t size, uint32_t handle);
typedef void (*fs_close_fn)(void *fs, uint32_t handle, uint32_t x);
typedef void (*fs_remove_fn)(void *fs, const char *path);
#define FS_REMOVE           0x14u
#define FS_OPEN             0x2Cu
#define FS_READ             0x30u
#define FS_WRITE            0x34u
#define FS_CLOSE            0x40u
#define FS_MODE_READ        0u
#define FS_MODE_WRITE       3u              /* FatFs create-always + RW, as the pattern save */

#define FILE_MAGIC          0x314E4C54u     /* "TLN1" */
#define FILE_HEADER         8u              /* magic, the number of entries */
#define ENTRY_SIZE          (1u + TRACKS)   /* the pattern's number, its 16 lengths */
#define CHUNK_ENTRIES       15u             /* 255 bytes a read or write */

static uint32_t fnv1a(uint32_t h, uint32_t byte)
{
    return (h ^ byte) * 16777619u;
}

static uint32_t data_sum(uint32_t h, const uint8_t *v, uint32_t n)
{
    for (uint32_t i = 0; i < n; i++)
        h = fnv1a(h, v[i]);
    return h;
}

static void put32(uint8_t *b, uint32_t v)
{
    b[0] = (uint8_t)v; b[1] = (uint8_t)(v >> 8); b[2] = (uint8_t)(v >> 16); b[3] = (uint8_t)(v >> 24);
}

static uint32_t get32(const uint8_t *b)
{
    return b[0] | b[1] << 8 | b[2] << 16 | (uint32_t)b[3] << 24;
}

/* At most the lengths the firmware keeps: the Save As request copies the name before it
 * writes its terminating 0. */
static uint32_t project_session(void)
{
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < NAME_MAX_LEN && FW_PROJECT_NAME[i]; i++)
        h = fnv1a(h, FW_PROJECT_NAME[i]);
    h = fnv1a(h, 0);
    for (uint32_t i = 0; i < PATH_MAX_LEN && FW_PROJECT_PATH[i]; i++)
        h = fnv1a(h, FW_PROJECT_PATH[i]);
    return fnv1a(h, 0) | 1;                 /* never 0 */
}

/* A project with no folder yet: no name (while Workspace loads), or the new project
 * 0x60117c8c makes, "New Project" with no path. */
static int project_unsaved(void)
{
    static const char fresh[] = "New Project";
    if (!FW_PROJECT_NAME[0])
        return 1;
    if (FW_PROJECT_PATH[0])
        return 0;
    for (uint32_t i = 0; i < sizeof fresh; i++)
        if (FW_PROJECT_NAME[i] != (uint8_t)fresh[i])
            return 0;
    return 1;
}

static int lengths_path(char *path, uint32_t size)
{
    if (project_unsaved())
        return 0;
    int n = ((snprintf_fn)FW_SNPRINTF)(path, size, "%s/%s/tracklen.bin",
                                       (const char *)FW_PROJECT_PATH, (const char *)FW_PROJECT_NAME);
    return n > 0 && (uint32_t)n < size;
}

static int file_manager_idle(void)
{
    const volatile uint8_t *fm = (const volatile uint8_t *)FW_FM;
    uint8_t state = fm[FM_STATE];
    return (state == 0 || state == 0x0C) && (fm[FM_SUBSTATE] & 0xFD) == 0;
}

static void clear_lengths(void)
{
    uint8_t *len = &TL->len[0][0];
    for (uint32_t i = 0; i < sizeof TL->len; i++)
        len[i] = 0;
}

/* Read the file through once: check it (apply = 0), or take its lengths (apply = 1).
 * 1: a whole, valid file. */
static int read_lengths(void *fs, const char *path, int apply)
{
    void **vt = *(void ***)fs;
    struct fs_open_result res = {0, 0, {0, 0, 0}};
    ((fs_open_fn)vt[FS_OPEN / 4])(&res, fs, path, FS_MODE_READ, 0);
    if (!res.ok)
        return 0;
    uint8_t buf[CHUNK_ENTRIES * ENTRY_SIZE];
    int ok = ((fs_read_fn)vt[FS_READ / 4])(fs, buf, FILE_HEADER, res.handle) == (int)FILE_HEADER
             && get32(buf) == FILE_MAGIC && get32(buf + 4) < PATTERNS;
    uint32_t left = ok ? get32(buf + 4) : 0, sum = data_sum(2166136261u, buf + 4, 4);
    while (ok && left) {
        uint32_t n = left < CHUNK_ENTRIES ? left : CHUNK_ENTRIES;
        ok = ((fs_read_fn)vt[FS_READ / 4])(fs, buf, n * ENTRY_SIZE, res.handle) == (int)(n * ENTRY_SIZE);
        for (uint32_t i = 0; ok && i < n; i++) {
            const uint8_t *entry = buf + ENTRY_SIZE * i;
            ok = entry[0] != 0;             /* patterns 1..255 */
            if (ok && apply)
                for (uint32_t t = 0; t < TRACKS; t++)
                    TL->len[entry[0]][t] = entry[1 + t];
        }
        sum = data_sum(sum, buf, n * ENTRY_SIZE);
        left -= n;
    }
    ok = ok && ((fs_read_fn)vt[FS_READ / 4])(fs, buf, 4, res.handle) == 4 && get32(buf) == sum;
    ((fs_close_fn)vt[FS_CLOSE / 4])(fs, res.handle, 1);
    return ok;
}

/* The project's lengths into the table (cleared before). A missing, damaged or short
 * file: none. The file is checked whole before any of it is used. */
static void load_lengths(void)
{
    char path[256];
    void *fs = *(void **)FW_FM;
    if (!fs || !lengths_path(path, sizeof path))
        return;
    if (read_lengths(fs, path, 0) && !read_lengths(fs, path, 1))
        clear_lengths();                    /* the second read failed */
}

static uint32_t has_lengths(uint32_t pattern)
{
    uint32_t any = 0;
    for (uint32_t t = 0; t < TRACKS; t++)
        any |= TL->len[pattern][t];
    return any;
}

/* 1: written (or, with no lengths, removed) */
static int save_lengths(void)
{
    char path[256];
    void *fs = *(void **)FW_FM;
    if (!fs || !lengths_path(path, sizeof path))
        return 0;
    void **vt = *(void ***)fs;
    uint32_t count = 0;
    for (uint32_t p = 1; p < PATTERNS; p++)
        count += has_lengths(p) != 0;
    ((fs_remove_fn)vt[FS_REMOVE / 4])(fs, path);
    if (!count)
        return 1;
    struct fs_open_result res = {0, 0, {0, 0, 0}};
    ((fs_open_fn)vt[FS_OPEN / 4])(&res, fs, path, FS_MODE_WRITE, 0);
    if (!res.ok)
        return 0;
    uint8_t buf[CHUNK_ENTRIES * ENTRY_SIZE];
    put32(buf, FILE_MAGIC);
    put32(buf + 4, count);
    uint32_t sum = data_sum(2166136261u, buf + 4, 4);
    int ok = ((fs_write_fn)vt[FS_WRITE / 4])(fs, buf, FILE_HEADER, res.handle) == (int)FILE_HEADER;
    uint32_t n = 0;
    for (uint32_t p = 1; ok && p <= PATTERNS; p++) {
        if (p < PATTERNS && has_lengths(p)) {
            uint8_t *entry = buf + ENTRY_SIZE * n++;
            entry[0] = (uint8_t)p;
            for (uint32_t t = 0; t < TRACKS; t++)
                entry[1 + t] = TL->len[p][t];
        }
        if (n && (n == CHUNK_ENTRIES || p == PATTERNS)) {
            sum = data_sum(sum, buf, n * ENTRY_SIZE);
            ok = ((fs_write_fn)vt[FS_WRITE / 4])(fs, buf, n * ENTRY_SIZE, res.handle) == (int)(n * ENTRY_SIZE);
            n = 0;
        }
    }
    put32(buf, sum);
    ok = ok && ((fs_write_fn)vt[FS_WRITE / 4])(fs, buf, 4, res.handle) == 4;
    ((fs_close_fn)vt[FS_CLOSE / 4])(fs, res.handle, 1);
    return ok;
}

/* At most one file read or write per call. 1: the lengths were (re)loaded. */
static int lengths_tick(void)
{
    tl_init_if_needed();
    if (!*(void **)FW_FM)
        return 0;
    uint32_t session = project_session();
    int unsaved = project_unsaved();
    if (session != TL->session) {
        int save_as = session == TL->save_as;   /* the same project under its new name */
        TL->save_as = 0;
        TL->session = session;
        TL->wait = SAVE_DELAY;
        TL->load = !(unsaved || save_as);
        TL->changed = (uint8_t)save_as;     /* after a Save As: written to the new folder */
        if (!save_as)                       /* nothing of the old project plays on */
            clear_lengths();
    }
    if (!file_manager_idle())
        return 0;
    if (TL->load) {
        TL->load = 0;
        load_lengths();
        TL->changed = 0;                    /* nothing to write until a length changes */
        return 1;
    }
    if (unsaved || !TL->changed) {
        TL->wait = SAVE_DELAY;
        return 0;
    }
    if (TL->wait) {
        TL->wait--;
        return 0;
    }
    TL->wait = SAVE_DELAY;
    if (save_lengths())
        TL->changed = 0;
    return 0;
}

/* Pattern screen vtable slot 0x600403dc (update method 0x600e1f99; chained). Lengths
 * loaded while the screen is up refill and redraw the grid. On page 3, Trk Len's second line follows
 * the cursor's track, the pattern and its length (redrawn only when one changes). */
void mt_tl_pattern_update(void *screen, void *a1, void *a2, void *a3)
{
    int loaded = lengths_tick();
    ((update_fn)mt_tl_next_pattern_update)(screen, a1, a2, a3);
    if (loaded)
        tl_refill_grid();
    uint8_t key[3];
    label_key(key);
    if ((key[0] != TL->shown[0] || key[1] != TL->shown[1] || key[2] != TL->shown[2]) && tl_bound())
        tl_show_label();
}

/* Config screen vtable slot 0x6003b5f8 (update method 0x600c6475; chained): projects are
 * opened and saved from there */
void mt_tl_config_update(void *screen, void *a1, void *a2, void *a3)
{
    lengths_tick();
    ((update_fn)mt_tl_next_config_update)(screen, a1, a2, a3);
}

/* 0x60114eaa in the save request 0x60114e34: "bl strncpy(fm + 0x340, name, 0x21)",
 * run only for Save As, after the new path is copied. The next tick sees the new
 * session and keeps the lengths for it. */
char *mt_tl_save_name(char *dst, const char *src, uint32_t n)
{
    char *r = ((strncpy_fn)mt_tl_next_save_name)(dst, src, n);
    tl_init_if_needed();
    TL->save_as = project_session();
    return r;
}

/* Config's version text: 1.2.0-tl1 */
const char mt_version_fmt[] = "%d.%d.%d-" TRACKLEN_VERSION;
