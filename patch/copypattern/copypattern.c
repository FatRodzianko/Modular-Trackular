/*
 * copypattern: copy the current pattern into the next empty one (Polyend Tracker+
 * firmware 1.2.0).
 *
 * The button. Page 3 of the Pattern screen's bottom bar has Pattern Name, Track Name,
 * five unused buttons and More. This patch puts "Copy to / New" on button 2, the first
 * unused one, or on button 3 when humanize is installed (humanize takes button 2): it
 * reads where button 2's label call (0x600e5884) goes, stock or a patch's hook. Its
 * hook sites are its own, none of them humanize's:
 *  - mt_cp_bind replaces the call that binds button 1 on page 3 (0x600e29c6): it
 *    binds button 1 as before, then, in record mode, its button to mt_cp_button;
 *  - mt_cp_label / mt_cp_label2 replace button 3's two (empty) text calls in page 3's
 *    label code (0x600e5890, 0x600e58f0), which come after button 2's;
 *  - mt_cp_colors / mt_cp_data replace button 0's colors and data calls (0x600e592e,
 *    0x600e5958) and give its button the same data, with the active colors in record
 *    mode and the greyed-out ones otherwise.
 * Like the stock editing buttons it only works in record mode. Toggling record
 * redraws the current page, which runs these hooks again.
 *
 * The copy. The press only records the current pattern (no file I/O there). The
 * Pattern screen's update method (mt_cp_pattern_update) then does the work, one
 * file at a time:
 *  - Search: from the next pattern up to 255, then from 1, for one that is empty:
 *    its track-usage mask is 0, it is not in the song's playlist or queued, and it
 *    has no file in Workspace/patterns, or a file whose steps are all empty (no note,
 *    no instrument, no FX type, on any of the 128 steps of any track). Patterns
 *    rejected from RAM cost nothing; each one that needs the card (exists, then a
 *    read in pieces of 256 bytes) takes one update.
 *  - Write, in the same update as the look that found it empty: if the current
 *    pattern is still the one pressed, the stock pattern_write_file writes the play
 *    buffer (the editor's pattern, with any unsaved edits) as the empty pattern's
 *    file, header and CRC included. Then the stock usage update sets its Song screen
 *    mask, and the project is marked as having unsaved changes, as a stock pattern
 *    save does.
 * The copy is dropped if the current pattern changes before it is written ("Pattern
 * changed - try again"), and silently if another project is opened meanwhile.
 * Nothing in the sequencer changes, so the screen stays on the current pattern,
 * playing or not. The stock message bar reports the result. Each step waits while
 * the file manager is busy (a project save or open, a pattern load).
 *
 * Shared hooks. All six sites are chained: they call through chain words
 * (mt_cp_next_*) that tools/patch_ptf.py fills with what the site pointed to before,
 * the stock function or another patch's hook. The Pattern screen update slot is
 * shared with humanize, interpolate, masterfx and swing.
 */
#include <stdint.h>

#ifndef COPYPATTERN_VERSION
#define COPYPATTERN_VERSION "cp4"
#endif

/* COPYPATTERN_PASSTHROUGH: diagnostic build where every hook only calls the original
 * (no button, no labels, no RAM, no file I/O), to test the hooking itself on hardware. */
#ifdef COPYPATTERN_PASSTHROUGH
#define PASSTHROUGH 1
#else
#define PASSTHROUGH 0
#endif

/* ---- firmware 1.2.0 addresses (function pointers carry the Thumb bit) ----------- */
#define FW_FM               0x2001E488u     /* file manager object; *FW_FM = FS object */
#define FW_PROJECT_PATH     ((const uint8_t *)(FW_FM + 0x241u))
#define FW_PROJECT_NAME     ((const uint8_t *)(FW_FM + 0x340u))
#define FM_STATE            0x13Du          /* file manager state; 0 or 0x0c when idle */
#define FM_SUBSTATE         0x13Fu
#define FW_SEQ              0x20017274u     /* sequencer */
#define SEQ_PLAY_BUFFER     0x14u           /* -> the current pattern's data */
#define SEQ_QUEUED          0x26u           /* queued pattern number ... */
#define SEQ_QUEUE_ACTIVE    0x28u           /* ... while this is set */
#define FW_PROJECT          0x20204000u
#define PROJECT_PLAYLIST    0x5C2u          /* 255 pattern numbers, 0 = none */
#define PROJECT_PATTERN     0x6C8u          /* current pattern number */
#define PROJECT_UNSAVED     0x6F4u          /* bit 0: unsaved changes (kept in project.mt) */
#define PROJECT_MASK_LO     0x8CCu          /* + n: tracks 0-7 of pattern n used */
#define PROJECT_MASK_HI     0xBF6u          /* + n: tracks 8-15 */
#define FW_PATVIEW          0x20021770u     /* Pattern screen object */
#define FW_ED               (FW_PATVIEW + 0x1000u)  /* its editor state */
#define FW_MESSAGE_BAR      0x20020880u

#define FW_WRITE_PATTERN    0x600C1FB5u     /* (fm, path, data): header, CRC, write -> 1 ok */
#define FW_USAGE_UPDATE     0x600C3999u     /* (fm, n, data): track-usage mask -> changed */
#define FW_MARK_UNSAVED     0x600BCD31u     /* (fm): fm+0x469 = 1 */
#define FW_SHOW_MESSAGE     0x600CDF29u     /* (bar, 0, text, 0) */
#define FW_BIND_BUTTON      0x600CF6EDu     /* (manager, index, flag, handler) */
#define FW_SET_COLORS       0x60101DA5u     /* disp_ctrl_set_colors(display, ctrl, colors) */
#define FW_SET_DATA         0x60101DF9u     /* disp_ctrl_set_data(display, ctrl, data) */
#define FW_SET_TEXT         0x60101D5Du     /* disp_ctrl_set_text(display, ctrl, text) */
#define FW_SET_TEXT2        0x60101D75u     /* disp_ctrl_set_ptr38(display, ctrl, text): 2nd line */
#define FW_SNPRINTF         0x6016712Du

/* editor state (FW_ED + offset) */
#define ED_RECORD           0x254u          /* record mode: editing buttons are active */
#define ED_BUTTON_CTRL      0x292u          /* int8 control index of bottom-bar button 0..7 */

/* display: *(display + 8) -> color sets for the bottom-bar labels */
#define DISP_STYLES         8u
#define STYLE_BUTTON        0x44u           /* active button */
#define STYLE_BUTTON_OFF    0x54u           /* greyed out */

/* pattern file: 0x1c-byte header, 16 tracks of 0x301 bytes (last-step byte, then
 * 128 steps of note, instrument, fx1 type, fx1 value, fx2 type, fx2 value), CRC */
#define PAT_ID              0x0002534Bu     /* header +0 */
#define PAT_VERSION_OFF     8u
#define PAT_VERSION         5u
#define PAT_SIZE_OFF        0xCu            /* u16 */
#define PAT_TRACKS_OFF      0x1Cu
#define PAT_TRACK_SIZE      0x301u
#define PAT_DATA_END        0x302Cu         /* then the CRC */
#define PAT_FILE_SIZE       0x3030u
#define PAT_EMPTY_NOTE      0xFFu

#define MAX_PATTERN         255u
#define FREE_BUTTON         2u              /* page 3's first unused button ... */
#define HZ_BUTTON           3u              /* ... and the next, when humanize has button 2 */
#define BUTTON2_TEXT_CALL   0x600E5884u     /* page 3: "bl disp_ctrl_set_text" for button 2 */
#define STOCK_SET_TEXT      0x60101D5Cu     /* ... which goes here unless a patch hooked it */
#define READ_PIECE          256u
#define BUSY_LIMIT          2000u           /* updates to wait for the file manager */

/* FS object methods (offsets in its vtable), as colorsong uses them */
struct fs_open_result { uint32_t handle; uint8_t ok; uint8_t pad[3]; };
typedef int (*fs_exists_fn)(void *fs, const char *path);
typedef void (*fs_open_fn)(struct fs_open_result *res, void *fs, const char *path, uint32_t mode, uint32_t x);
typedef int (*fs_read_fn)(void *fs, void *buf, uint32_t size, uint32_t handle);
typedef void (*fs_close_fn)(void *fs, uint32_t handle, uint32_t x);
typedef void (*fs_remove_fn)(void *fs, const char *path);
#define FS_EXISTS           0x08u
#define FS_REMOVE           0x14u
#define FS_OPEN             0x2Cu
#define FS_READ             0x30u
#define FS_CLOSE            0x40u
#define FS_MODE_READ        0u

typedef void (*void_fn)(void);
typedef int (*write_fn)(uint32_t fm, const char *path, uint8_t *data);
typedef int (*usage_fn)(uint32_t fm, uint32_t n, const uint8_t *data);
typedef void (*obj_fn)(uint32_t obj);
typedef void (*message_fn)(uint32_t bar, uint32_t x, const char *text, uint32_t time);
typedef void (*bind_fn)(void *manager, uint32_t index, uint32_t flag, void_fn handler);
typedef void (*ctrl_fn)(void *display, int ctrl, const void *arg);
typedef int (*snprintf_fn)(char *buf, uint32_t size, const char *fmt, ...);
typedef void (*update_fn)(void *screen, void *a1, void *a2, void *a3);

void mt_cp_button(void);

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_cp_next_bind);                /* btn_bind 0x600cf6ed */
CHAIN_WORD(mt_cp_next_label);               /* disp_ctrl_set_text 0x60101d5d */
CHAIN_WORD(mt_cp_next_label2);              /* disp_ctrl_set_ptr38 0x60101d75 */
CHAIN_WORD(mt_cp_next_colors);              /* disp_ctrl_set_colors 0x60101da5 */
CHAIN_WORD(mt_cp_next_data);                /* disp_ctrl_set_data 0x60101df9 */
CHAIN_WORD(mt_cp_next_pattern_update);      /* Pattern screen update 0x600e1f99 */

/* ---- state ----------------------------------------------------------------------- */

#define CP_MAGIC            0x5043544Du     /* "MTCP" */

struct cp_state {
    uint32_t magic;
    uint32_t session;                       /* project name + path at the press */
    uint8_t copying;                        /* a copy is under way */
    uint8_t source;                         /* the pattern on screen at the press */
    uint8_t next;                           /* next pattern to look at */
    uint8_t left;                           /* patterns left to look at */
    uint16_t busy;                          /* updates spent waiting for the file manager */
    uint16_t reserved;
    char message[32];                       /* the message bar keeps pointing at it */
};
/* This patch's RAM (.mt_ram, neither loaded nor zeroed; tools/patch_ptf.py places it in
 * a free RAM region). */
static struct cp_state cp_state_ram __attribute__((section(".mt_ram")));
#define CP ((volatile struct cp_state *)&cp_state_ram)

static void cp_init_if_needed(void)         /* RAM outside .bss holds garbage at power-on */
{
    if (CP->magic == CP_MAGIC)
        return;
    CP->copying = 0;
    CP->busy = 0;
    CP->message[0] = 0;
    CP->magic = CP_MAGIC;
}

static int record_mode(void)
{
    return *(const volatile uint8_t *)(FW_ED + ED_RECORD) != 0;
}

static const volatile uint8_t *project(void)
{
    return (const volatile uint8_t *)FW_PROJECT;
}

static void finish(const char *fmt, uint32_t n)
{
    char *msg = (char *)CP->message;
    ((snprintf_fn)FW_SNPRINTF)(msg, sizeof cp_state_ram.message, fmt, n);
    ((message_fn)FW_SHOW_MESSAGE)(FW_MESSAGE_BAR, 0, msg, 0);
    CP->copying = 0;
}

static uint32_t fnv1a(uint32_t h, uint32_t byte)
{
    return (h ^ byte) * 16777619u;
}

/* The project the copy belongs to: its name and folder, as humanize tells projects apart */
static uint32_t project_session(void)
{
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < 64 && FW_PROJECT_NAME[i]; i++)
        h = fnv1a(h, FW_PROJECT_NAME[i]);
    h = fnv1a(h, 0);
    for (uint32_t i = 0; i < 255 && FW_PROJECT_PATH[i]; i++)
        h = fnv1a(h, FW_PROJECT_PATH[i]);
    return fnv1a(h, 0);
}

/* ---- the press ----------------------------------------------------------------- */

void mt_cp_button(void)
{
    if (!record_mode())                     /* only bound in record mode; just in case */
        return;
    cp_init_if_needed();
    if (CP->copying)                        /* a copy is already under way */
        return;
    uint32_t source = project()[PROJECT_PATTERN];
    if (source < 1 || source > MAX_PATTERN)
        return;
    CP->source = (uint8_t)source;
    CP->next = (uint8_t)(source % MAX_PATTERN + 1);
    CP->left = MAX_PATTERN - 1;
    CP->busy = 0;
    CP->session = project_session();
    CP->copying = 1;
}

/* ---- the scan ------------------------------------------------------------------ */

/* 1: pattern n is in use as far as RAM tells: its mask, the playlist, the queue */
static int used_in_ram(uint32_t n)
{
    const volatile uint8_t *p = project();
    if (p[PROJECT_MASK_LO + n] | p[PROJECT_MASK_HI + n])
        return 1;
    for (uint32_t i = 0; i < 255; i++)
        if (p[PROJECT_PLAYLIST + i] == n)
            return 1;
    const volatile uint8_t *seq = (const volatile uint8_t *)FW_SEQ;
    return seq[SEQ_QUEUE_ACTIVE] && seq[SEQ_QUEUED] == n;
}

static int pattern_path(char *path, uint32_t size, uint32_t n)
{
    int len = ((snprintf_fn)FW_SNPRINTF)(path, size, "Workspace/patterns/pattern_%02d.mtp", n);
    return len > 0 && (uint32_t)len < size;
}

static uint32_t get32(const uint8_t *b)
{
    return b[0] | b[1] << 8 | b[2] << 16 | (uint32_t)b[3] << 24;
}

/* 1: the file at `path` holds a current-version pattern whose steps are all empty */
static int file_is_empty(void *fs, const char *path)
{
    void **vt = *(void ***)fs;
    struct fs_open_result res = {0, 0, {0, 0, 0}};
    ((fs_open_fn)vt[FS_OPEN / 4])(&res, fs, path, FS_MODE_READ, 0);
    if (!res.ok)
        return 0;
    uint8_t buf[READ_PIECE];
    int empty = 1;
    uint32_t pos = 0;
    while (empty && pos < PAT_FILE_SIZE) {
        uint32_t want = PAT_FILE_SIZE - pos < READ_PIECE ? PAT_FILE_SIZE - pos : READ_PIECE;
        int got = ((fs_read_fn)vt[FS_READ / 4])(fs, buf, want, res.handle);
        if (got != (int)want) {
            empty = 0;                      /* short or failed: count it as used */
            break;
        }
        if (pos == 0 && (get32(buf) != PAT_ID || buf[PAT_VERSION_OFF] != PAT_VERSION
                         || (buf[PAT_SIZE_OFF] | buf[PAT_SIZE_OFF + 1] << 8) != PAT_FILE_SIZE))
            empty = 0;                      /* another file version: leave it alone */
        for (uint32_t i = 0; empty && i < want; i++) {
            uint32_t o = pos + i;
            if (o <= PAT_TRACKS_OFF || o >= PAT_DATA_END)
                continue;                   /* header, CRC */
            uint32_t in_track = (o - PAT_TRACKS_OFF) % PAT_TRACK_SIZE;
            if (in_track == 0)
                continue;                   /* the track's last-step byte */
            uint32_t field = (in_track - 1) % 6;
            uint8_t b = buf[i];
            if (field == 0 ? b != PAT_EMPTY_NOTE : (field == 1 || field == 2 || field == 4) && b != 0)
                empty = 0;                  /* a note, an instrument or an FX type */
        }
        pos += want;
    }
    ((fs_close_fn)vt[FS_CLOSE / 4])(fs, res.handle, 1);
    return empty;
}

/* ---- the write ----------------------------------------------------------------- */

/* The current pattern number and its data, read together: the sequencer's interrupt
 * swaps the buffers and sets the number at a pattern change. */
static uint32_t current_pattern(uint8_t **data)
{
    uint32_t primask;
    __asm__ volatile ("mrs %0, primask\n cpsid i" : "=r"(primask) :: "memory");
    uint32_t n = project()[PROJECT_PATTERN];
    *data = *(uint8_t *volatile *)(FW_SEQ + SEQ_PLAY_BUFFER);
    __asm__ volatile ("msr primask, %0" :: "r"(primask) : "memory");
    return n;
}

/* Write the current pattern as pattern `target` (at `path`), if it is still the source */
static void write_copy(void *fs, uint32_t target, const char *path)
{
    uint8_t *data;
    if (current_pattern(&data) != CP->source || !data) {
        finish("Pattern changed - try again", 0);
        return;
    }
    if (!((write_fn)FW_WRITE_PATTERN)(FW_FM, path, data)) {
        void **vt = *(void ***)fs;
        ((fs_remove_fn)vt[FS_REMOVE / 4])(fs, path);    /* no half-written pattern */
        finish("Copy failed - SD card", 0);
        return;
    }
    ((usage_fn)FW_USAGE_UPDATE)(FW_FM, target, data);
    ((obj_fn)FW_MARK_UNSAVED)(FW_FM);
    *(volatile uint8_t *)(FW_PROJECT + PROJECT_UNSAVED) |= 1;
    finish("Copied to pattern %d", target);
}

/* One update's worth of the search: skip what RAM rules out, then look at the card
 * for at most one pattern; if it is empty, write the copy over it right away, so
 * nothing can change it between the look and the write. */
static void scan(void *fs)
{
    while (CP->left) {
        uint32_t n = CP->next;
        CP->next = (uint8_t)(n % MAX_PATTERN + 1);
        CP->left--;
        if (n == CP->source || used_in_ram(n))
            continue;
        char path[64];
        if (!pattern_path(path, sizeof path, n))
            continue;
        void **vt = *(void ***)fs;
        if (!((fs_exists_fn)vt[FS_EXISTS / 4])(fs, path) || file_is_empty(fs, path))
            write_copy(fs, n, path);
        return;
    }
    finish("No empty pattern left", 0);
}

static int file_manager_idle(void)
{
    const volatile uint8_t *fm = (const volatile uint8_t *)FW_FM;
    uint8_t state = fm[FM_STATE];
    return (state == 0 || state == 0x0C) && (fm[FM_SUBSTATE] & 0xFD) == 0;
}

static void copy_tick(void)
{
    cp_init_if_needed();
    if (!CP->copying)
        return;
    if (project_session() != CP->session) {     /* another project was opened meanwhile */
        CP->copying = 0;
        return;
    }
    if (project()[PROJECT_PATTERN] != CP->source) {
        finish("Pattern changed - try again", 0);
        return;
    }
    void *fs = *(void **)FW_FM;
    if (!fs || !file_manager_idle()) {
        if (++CP->busy >= BUSY_LIMIT)
            finish("Busy - try again", 0);
        return;
    }
    CP->busy = 0;
    scan(fs);
}

/* Pattern screen vtable slot 0x600403dc (update method 0x600e1f99; chained) */
void mt_cp_pattern_update(void *screen, void *a1, void *a2, void *a3)
{
    if (!PASSTHROUGH)
        copy_tick();
    ((update_fn)mt_cp_next_pattern_update)(screen, a1, a2, a3);
}

/* ---- the button ---------------------------------------------------------------- */

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

/* The button this patch takes: button 2, the first unused one, unless another patch
 * already labels it (humanize hooks its text call); then button 3. The flash doesn't
 * change, so every call gives the same answer, in any bundle order. */
static uint32_t cp_button(void)
{
    return bl_target(BUTTON2_TEXT_CALL) == STOCK_SET_TEXT ? FREE_BUTTON : HZ_BUTTON;
}

static int button_ctrl(uint32_t button)
{
    return *(const volatile int8_t *)(FW_ED + ED_BUTTON_CTRL + button);
}

static int cp_ctrl(void)
{
    return button_ctrl(cp_button());
}

/* replaces "bl btn_bind" at 0x600e29c6 (page 3: bind button 1 = Track Name). The
 * page's binding has just cleared buttons 0..6, so outside record mode the button
 * stays unbound. */
void mt_cp_bind(void *manager, uint32_t index, uint32_t flag, void_fn handler)
{
    ((bind_fn)mt_cp_next_bind)(manager, index, flag, handler);
    if (!PASSTHROUGH && record_mode())
        ((bind_fn)FW_BIND_BUTTON)(manager, cp_button(), 1, mt_cp_button);
}

/* replaces "bl disp_ctrl_set_text" at 0x600e5890 (page 3: button 3's empty text). It
 * runs after button 2's own call, so on button 2 this text replaces the empty one. */
void mt_cp_label(void *display, int ctrl, const void *text)
{
    if (PASSTHROUGH || cp_button() != HZ_BUTTON) {
        ((ctrl_fn)mt_cp_next_label)(display, ctrl, text);
        if (!PASSTHROUGH)
            ((ctrl_fn)FW_SET_TEXT)(display, button_ctrl(FREE_BUTTON), "Copy to");
        return;
    }
    ((ctrl_fn)mt_cp_next_label)(display, ctrl, "Copy to");
}

/* replaces "bl disp_ctrl_set_ptr38" at 0x600e58f0 (page 3: button 3's empty second
 * line), the same way */
void mt_cp_label2(void *display, int ctrl, const void *text)
{
    if (PASSTHROUGH || cp_button() != HZ_BUTTON) {
        ((ctrl_fn)mt_cp_next_label2)(display, ctrl, text);
        if (!PASSTHROUGH)
            ((ctrl_fn)FW_SET_TEXT2)(display, button_ctrl(FREE_BUTTON), "New");
        return;
    }
    ((ctrl_fn)mt_cp_next_label2)(display, ctrl, "New");
}

/* replaces "bl disp_ctrl_set_colors" at 0x600e592e (page 3: Pattern Name's colors).
 * This patch's button gets the active set in record mode and the greyed-out set
 * otherwise. */
void mt_cp_colors(void *display, int ctrl, const void *colors)
{
    ((ctrl_fn)mt_cp_next_colors)(display, ctrl, colors);
    if (PASSTHROUGH)
        return;
    const uint8_t *styles = *(const uint8_t *const *)((const uint8_t *)display + DISP_STYLES);
    ((ctrl_fn)FW_SET_COLORS)(display, cp_ctrl(),
                             styles + (record_mode() ? STYLE_BUTTON : STYLE_BUTTON_OFF));
}

/* replaces "bl disp_ctrl_set_data" at 0x600e5958 (page 3: Pattern Name's data) */
void mt_cp_data(void *display, int ctrl, const void *data)
{
    ((ctrl_fn)mt_cp_next_data)(display, ctrl, data);
    if (PASSTHROUGH)
        return;
    ((ctrl_fn)FW_SET_DATA)(display, cp_ctrl(), data);
}

/* ---- version text: Config shows 1.2.0-<COPYPATTERN_VERSION> ---------------------- */
/* COPYPATTERN_PAD=n: diagnostic build with n bytes of 0xFF before the version text, to
 * put the text alone at a chosen flash address (the flash-sector test of a pass-through
 * build). The text is then plain "1.2.0-..." with no conversions, so that bytes read
 * back wrong can't turn into one that reads memory. */
#ifdef COPYPATTERN_PAD
#define CP_STR2(x) #x
#define CP_STR(x) CP_STR2(x)
__asm__(".section .rodata.mt_cp_version, \"a\"\n"
        ".fill " CP_STR(COPYPATTERN_PAD) ", 1, 0xff\n"
        ".global mt_version_fmt\n"
        "mt_version_fmt: .asciz \"1.2.0-" COPYPATTERN_VERSION "\"\n"
        ".previous\n");
#else
const char mt_version_fmt[] = "%d.%d.%d-" COPYPATTERN_VERSION;
#endif
