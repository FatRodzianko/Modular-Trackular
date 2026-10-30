/*
 * swing: Global Swing (Polyend Tracker+ firmware 1.2.0).
 *
 * Stock firmware sets swing only with the Swing FX (I, 25..75, 50 = none): its handler
 * (ITCM 0x2f10) turns swing on (seq+0x60) and stores the amount (seq+0x64, a float);
 * the tick period then alternates (s + 50) and (150 - s) per step, by a phase that track
 * 16 toggles at every step end (seq+0x61). Nothing resets swing when playback starts, so
 * it carries over from whatever played last, and a Swing FX on a muted track is skipped:
 * takes can start with different swing.
 *
 * With Global Swing on (Project Settings > Global Swing), swing is set to the project's
 * Swing amount, starting on the long step:
 *  - when playback starts (seen at the start of the next audio block, before the
 *    sequencer runs: mt_sw_seq_advance on the seq_advance call, chained);
 *  - when a pattern starts again: after track 16's step end (seq_advance_step,
 *    0x600ad010) wraps back to the pattern start, a new pass of the loop or the next
 *    pattern in song mode (mt_sw_advance_step, on the word the sequencer's ITCM code
 *    jumps through);
 *  - when Global Swing is turned on or the amount changed.
 * A Swing FX still works, until the pattern starts again. Turning Global Swing off sets
 * swing back to none once; from then on the Swing FX works as in stock firmware.
 * Performance mode, whose tracks may step backwards or at random, gets the play-start
 * reset only. The render loop (rendering to a file) calls seq_advance through its own
 * call site; that one is hooked too.
 *
 * The settings: a "Global Swing" folder in Project Settings with "Global swing" (Off/On,
 * Off by default) and "Swing amount" (25..75, 50 by default). Each saved project keeps
 * them in <project folder>/swing.bin, read and written from the Pattern and Config
 * screens' update methods, as humanize and interpolate do, only while the file manager
 * is idle:
 *  - another project: the defaults at once, then its file once the file manager is idle;
 *  - Save As (and a new project's first save, which is one): the firmware copies only
 *    its own files to the new folder, so the settings go with the project to its new
 *    name and are written there. The Save As request's name copy (0x60114eaa) marks it;
 *  - a new project ("New Project", no path) has no folder yet: nothing is written.
 *
 * Shared hooks go through chain words (mt_sw_next_*) that tools/patch_ptf.py fills with
 * what the site pointed to before, so other patches can hook them too.
 */
#include <stdint.h>

#ifndef SWING_VERSION
#define SWING_VERSION "sw3"
#endif

/* ---- firmware 1.2.0 addresses (function pointers carry the Thumb bit) ----------- */
#define FW_FM               0x2001E488u     /* file manager object; *FW_FM = FS object */
#define FM_STATE            0x13Du          /* file manager state; 0 or 0x0c when idle */
#define FM_SUBSTATE         0x13Fu
#define FW_PROJECT_PATH     ((const uint8_t *)(FW_FM + 0x241u))     /* 254 characters and a 0 */
#define FW_PROJECT_NAME     ((const uint8_t *)(FW_FM + 0x340u))     /* 32 characters and a 0 */
#define PATH_MAX_LEN        254u
#define NAME_MAX_LEN        32u
#define FW_PROJECT_SETTINGS 0x20028714u     /* Config menu folder "Project Settings" */
#define FW_MENU_FOLDER      0x60118395u     /* (node, parent, label, capacity) */
#define FW_MENU_ITEM        0x601183E9u     /* (node, parent, label, help, type, descriptor) */
#define FW_MALLOC           0x600A3589u     /* (size) */
#define FW_SNPRINTF         0x6016712Du
#define FW_OFF_ON           0x60060B88u     /* option names {"Off", "On"} */

/* sequencer object */
#define SEQ_PERFORMANCE     0x1Du
#define SEQ_PLAYING         0x2Au
#define SEQ_SWING_ON        0x60u           /* byte */
#define SEQ_SWING_PHASE     0x61u           /* byte: 0 the long step, 1 the short one */
#define SEQ_SWING           0x64u           /* float 25..75 */
#define SEQ_TRACK(t)        (0x68u * (t))
#define TRK_STEP            0x88u           /* uint16: current step */
#define LAST_TRACK          15u

#define SWING_MIN           25u
#define SWING_MAX           75u
#define SWING_NONE          50u

typedef void (*void_fn)(void);
typedef void (*advance_fn)(void *seq, float seconds);
typedef uint32_t (*advance_step_fn)(void *seq, uint32_t track);
typedef void (*update_fn)(void *screen, void *a1, void *a2, void *a3);
typedef uint32_t (*project_update_fn)(void *screen, void *a1, void *a2, void *a3);
typedef void *(*folder_fn)(void *node, void *parent, const char *label, uint32_t capacity);
typedef void *(*item_fn)(void *node, void *parent, const char *label, const char *help,
                         uint32_t type, const void *descriptor);
typedef void *(*malloc_fn)(uint32_t size);
typedef int (*snprintf_fn)(char *buf, uint32_t size, const char *fmt, ...);
typedef char *(*strncpy_fn)(char *dst, const char *src, uint32_t n);

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_sw_next_seq_advance);         /* seq_advance (veneer 0x600aa859) */
CHAIN_WORD(mt_sw_next_render_advance);      /* seq_advance in the render loop (veneer 0x600bc2d9) */
CHAIN_WORD(mt_sw_next_advance_step);        /* seq_advance_step 0x600ad011 */
CHAIN_WORD(mt_sw_next_folder);              /* Project Settings: cfg_menu_folder 0x60118395 */
CHAIN_WORD(mt_sw_next_last_item);           /* last item: cfg_menu_item 0x601183e9 */
CHAIN_WORD(mt_sw_next_pattern_update);      /* Pattern screen update 0x600e1f99 */
CHAIN_WORD(mt_sw_next_config_update);       /* Config screen update 0x600c6475 */
CHAIN_WORD(mt_sw_next_save_name);           /* strncpy 0x601673d7 (Save As: the new name) */
CHAIN_WORD(mt_sw_next_project_update);      /* project screen update 0x600ead59 */

/* ---- state (.mt_ram: neither loaded nor zeroed; the patcher places it) ----------- */

#define SW_MAGIC            0x32534D47u     /* "GMS2": the state's layout of sw2 */
#define SAVE_DELAY          25u             /* screen updates between a change and the save */

struct sw_state {
    uint32_t magic;
    uint32_t session;                       /* project name + path the settings belong to */
    uint8_t on;                             /* Global swing: 0 Off, 1 On (the menu edits it) */
    uint8_t amount;                         /* Swing amount - 25, 0..50 (the menu edits it) */
    uint8_t saved_on, saved_amount;         /* what swing.bin holds */
    uint8_t saved_valid;
    uint8_t load;                           /* the project's file is still to be read */
    uint8_t wait;                           /* updates left before saving */
    /* the audio side */
    uint8_t was_on;                         /* Global swing as last seen there */
    uint8_t was_playing;
    uint8_t applied;                        /* the amount last set */
    uint8_t pad[2];
    uint32_t save_as;                       /* the session a Save As moves the settings to; 0: none */
};
static struct sw_state sw_state_ram __attribute__((section(".mt_ram")));
#define SW (&sw_state_ram)

static void sw_init_if_needed(void)         /* RAM outside .bss holds garbage at power-on */
{
    if (SW->magic == SW_MAGIC)
        return;
    SW->on = 0;
    SW->amount = SWING_NONE - SWING_MIN;
    SW->session = 0;
    SW->saved_valid = 0;
    SW->load = 0;
    SW->wait = 0;
    SW->save_as = 0;
    SW->was_on = 0;
    SW->was_playing = 0;
    SW->applied = 0xFF;
    SW->magic = SW_MAGIC;
}

/* ---- the swing (audio interrupt) ------------------------------------------------- */

static inline uint32_t sw_amount(void)
{
    uint32_t a = *(volatile uint8_t *)&SW->amount;
    return SWING_MIN + (a > SWING_MAX - SWING_MIN ? SWING_NONE - SWING_MIN : a);
}

/* The global swing, from the long step (phase 0) if `restart`. */
static void sw_apply(uint8_t *seq, int restart)
{
    uint32_t amount = sw_amount();
    seq[SEQ_SWING_ON] = 1;
    *(volatile float *)(seq + SEQ_SWING) = (float)amount;
    if (restart)
        seq[SEQ_SWING_PHASE] = 0;
    SW->applied = (uint8_t)amount;
}

/* Before the sequencer runs, once per audio block: playback just started, Global swing
 * just turned on or off, the amount just changed. */
static void sw_block(uint8_t *seq)
{
    if (SW->magic != SW_MAGIC)
        return;
    int on = *(volatile uint8_t *)&SW->on == 1;
    int playing = seq[SEQ_PLAYING] != 0;
    if (on) {
        if (playing && !SW->was_playing)
            sw_apply(seq, 1);               /* a new take: the global swing from the start */
        else if (!SW->was_on || SW->applied != sw_amount())
            sw_apply(seq, 0);
    } else if (SW->was_on) {
        seq[SEQ_SWING_ON] = 0;              /* turned off: no swing, until a Swing FX */
        SW->applied = 0xFF;
    }
    SW->was_on = (uint8_t)on;
    SW->was_playing = (uint8_t)playing;
}

/* replaces "bl seq_advance" (veneer 0x600aa858) at 0x600aa7f8 in app_audio_block (chained) */
void mt_sw_seq_advance(void *seq, float seconds)
{
    sw_block((uint8_t *)seq);
    ((advance_fn)mt_sw_next_seq_advance)(seq, seconds);
}

/* replaces "bl seq_advance" (veneer 0x600bc2d8) at 0x600bc27a in the render loop (chained) */
void mt_sw_render_advance(void *seq, float seconds)
{
    sw_block((uint8_t *)seq);
    ((advance_fn)mt_sw_next_render_advance)(seq, seconds);
}

/* the word the sequencer's ITCM code jumps through to seq_advance_step(seq, track),
 * 0x601acf00 in flash (chained): after track 16 wraps, the global swing again */
uint32_t mt_sw_advance_step(void *seq, uint32_t track)
{
    uint8_t *s = (uint8_t *)seq;
    uint16_t before = *(volatile uint16_t *)(s + SEQ_TRACK(LAST_TRACK) + TRK_STEP);
    uint32_t r = ((advance_step_fn)mt_sw_next_advance_step)(seq, track);
    if (track == LAST_TRACK && SW->magic == SW_MAGIC && *(volatile uint8_t *)&SW->on == 1 && !s[SEQ_PERFORMANCE]
        && *(volatile uint16_t *)(s + SEQ_TRACK(LAST_TRACK) + TRK_STEP) <= before)
        sw_apply(s, 1);
    return r;
}

/* ---- the Config menu ------------------------------------------------------------ */

#define MENU_ITEM_SIZE      0x20u           /* fields up to +0x18 */
#define MENU_FOLDER_SIZE    0x20u           /* fields up to +0x1e */
#define MENU_NUMBER         2u              /* stock number item (e.g. Metronome Volume) */
#define MENU_OPTIONS        4u              /* stock option item (e.g. Anti-aliasing) */
#define FOLDER_ITEMS        2u              /* Global swing, Swing amount */

/* option item (type 4): value pointer, option count, option names, and a callback that
 * the menu's apply (cfg_menu_apply, 0x60118658) calls after storing the choice, without
 * checking it for NULL; number item (type 2): value pointer, min, max, step, and the
 * offset the list shows the stored index with */
struct menu_options { volatile uint8_t *value; uint32_t count; const char *const *names; void_fn on_apply; };
struct menu_number { volatile uint8_t *value; uint8_t min, max, step, offset; };

static void option_applied(void)            /* the audio side and settings_tick see the value */
{
}

static const struct menu_options on_desc = {&SW->on, 2, (const char *const *)FW_OFF_ON, option_applied};
static const struct menu_number amount_desc = {&SW->amount, 0, SWING_MAX - SWING_MIN, 1, SWING_MIN};

/* replaces "bl 0x60118394" at 0x60118b00 (chained): Project Settings gets room for the
 * Global Swing folder */
void *mt_sw_settings_folder(void *node, void *parent, const char *label, uint32_t capacity)
{
    return ((folder_fn)mt_sw_next_folder)(node, parent, label, capacity + 1);
}

/* replaces the constructor's last "bl 0x601183e8" (Licenses) at 0x601193e6 (chained) */
void *mt_sw_last_item(void *node, void *parent, const char *label, const char *help,
                      uint32_t type, const void *descriptor)
{
    void *r = ((item_fn)mt_sw_next_last_item)(node, parent, label, help, type, descriptor);
    sw_init_if_needed();
    SW->was_playing = 0;
    malloc_fn alloc = (malloc_fn)FW_MALLOC;
    void *folder = alloc(MENU_FOLDER_SIZE);
    if (folder) {
        ((folder_fn)FW_MENU_FOLDER)(folder, (void *)FW_PROJECT_SETTINGS, "Global Swing", FOLDER_ITEMS);
        void *item = alloc(MENU_ITEM_SIZE);
        if (item)
            ((item_fn)FW_MENU_ITEM)(item, folder, "Global swing",
                                    "On: every take and every pattern starts with the Swing amount "
                                    "below. A Swing FX still changes it until the pattern starts again.",
                                    MENU_OPTIONS, &on_desc);
        item = alloc(MENU_ITEM_SIZE);
        if (item)
            ((item_fn)FW_MENU_ITEM)(item, folder, "Swing amount",
                                    "25 to 75, as the Swing FX: 50 is no swing, above 50 the off-beat "
                                    "steps come later. Used when Global swing is On.",
                                    MENU_NUMBER, &amount_desc);
    }
    return r;
}

/* ---- swing.bin ------------------------------------------------------------------- */

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

#define FILE_MAGIC          0x31575348u     /* "HSW1" */
#define FILE_SIZE           12u             /* magic, on, amount - 25, 2 zero bytes, 4-byte checksum */

static uint32_t fnv1a(uint32_t h, uint32_t byte)
{
    return (h ^ byte) * 16777619u;
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

static uint32_t data_sum(const uint8_t *v)
{
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < 4; i++)
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

static int settings_path(char *path, uint32_t size)
{
    if (project_unsaved())
        return 0;
    int n = ((snprintf_fn)FW_SNPRINTF)(path, size, "%s/%s/swing.bin",
                                       (const char *)FW_PROJECT_PATH, (const char *)FW_PROJECT_NAME);
    return n > 0 && (uint32_t)n < size;
}

static int file_manager_idle(void)
{
    const volatile uint8_t *fm = (const volatile uint8_t *)FW_FM;
    uint8_t state = fm[FM_STATE];
    return (state == 0 || state == 0x0C) && (fm[FM_SUBSTATE] & 0xFD) == 0;
}

static void defaults(void)
{
    SW->on = 0;
    SW->amount = SWING_NONE - SWING_MIN;
}

/* 1: the file was read and applied */
static int settings_load(void)
{
    char path[256];
    void *fs = *(void **)FW_FM;
    if (!fs || !settings_path(path, sizeof path))
        return 0;
    void **vt = *(void ***)fs;
    struct fs_open_result res = {0, 0, {0, 0, 0}};
    uint8_t buf[FILE_SIZE];
    ((fs_open_fn)vt[FS_OPEN / 4])(&res, fs, path, FS_MODE_READ, 0);
    if (!res.ok)
        return 0;
    int ok = ((fs_read_fn)vt[FS_READ / 4])(fs, buf, FILE_SIZE, res.handle) == (int)FILE_SIZE;
    ((fs_close_fn)vt[FS_CLOSE / 4])(fs, res.handle, 1);
    ok = ok && get32(buf) == FILE_MAGIC && get32(buf + 8) == data_sum(buf + 4) && buf[4] <= 1
         && buf[5] <= SWING_MAX - SWING_MIN;
    if (!ok)
        return 0;
    SW->on = SW->saved_on = buf[4];
    SW->amount = SW->saved_amount = buf[5];
    SW->saved_valid = 1;
    return 1;
}

static void settings_save(void)
{
    char path[256];
    void *fs = *(void **)FW_FM;
    if (!fs || !settings_path(path, sizeof path))
        return;
    void **vt = *(void ***)fs;
    uint8_t buf[FILE_SIZE];
    uint8_t on = SW->on, amount = SW->amount;
    put32(buf, FILE_MAGIC);
    buf[4] = on;
    buf[5] = amount;
    buf[6] = buf[7] = 0;
    put32(buf + 8, data_sum(buf + 4));
    struct fs_open_result res = {0, 0, {0, 0, 0}};
    ((fs_remove_fn)vt[FS_REMOVE / 4])(fs, path);
    ((fs_open_fn)vt[FS_OPEN / 4])(&res, fs, path, FS_MODE_WRITE, 0);
    if (!res.ok)
        return;
    int ok = ((fs_write_fn)vt[FS_WRITE / 4])(fs, buf, FILE_SIZE, res.handle) == (int)FILE_SIZE;
    ((fs_close_fn)vt[FS_CLOSE / 4])(fs, res.handle, 1);
    if (ok) {
        SW->saved_on = on;
        SW->saved_amount = amount;
        SW->saved_valid = 1;
    }
}

/* Called from the Pattern and Config screens' update methods (where the stock code
 * does its own file I/O). At most one small file read or write per call. */
static void settings_tick(void)
{
    sw_init_if_needed();
    if (!*(void **)FW_FM)
        return;
    uint32_t session = project_session();
    int unsaved = project_unsaved();
    if (session != SW->session) {
        int save_as = session == SW->save_as;   /* the same project under its new name */
        SW->save_as = 0;
        SW->session = session;
        SW->saved_valid = 0;                /* after a Save As: written to the new folder */
        SW->wait = SAVE_DELAY;
        SW->load = (uint8_t)(!unsaved && !save_as);
        if (!save_as)
            defaults();                     /* another project: its file, once read, decides */
    }
    if (!file_manager_idle())
        return;
    if (SW->load) {
        SW->load = 0;
        settings_load();                    /* none or a bad one: the defaults */
        SW->saved_on = SW->on;
        SW->saved_amount = SW->amount;
        SW->saved_valid = 1;                /* nothing to write until a setting changes */
        return;
    }
    if (SW->on > 1)
        SW->on = 0;
    if (SW->amount > SWING_MAX - SWING_MIN)
        SW->amount = SWING_NONE - SWING_MIN;
    if (unsaved || (SW->saved_valid && SW->on == SW->saved_on && SW->amount == SW->saved_amount)) {
        SW->wait = SAVE_DELAY;
        return;
    }
    if (SW->wait) {
        SW->wait--;
        return;
    }
    SW->wait = SAVE_DELAY;
    settings_save();
}

/* Pattern screen vtable slot 0x600403dc (update method 0x600e1f99; chained) */
void mt_sw_pattern_update(void *screen, void *a1, void *a2, void *a3)
{
    settings_tick();
    ((update_fn)mt_sw_next_pattern_update)(screen, a1, a2, a3);
}

/* Config screen vtable slot 0x6003b5f8 (update method 0x600c6475; chained) */
void mt_sw_config_update(void *screen, void *a1, void *a2, void *a3)
{
    settings_tick();
    ((update_fn)mt_sw_next_config_update)(screen, a1, a2, a3);
}

/* Project screen vtable slot 0x6004193c (update method 0x600ead59; chained): New
 * Project, Open and Save As happen there, so a new project is seen before its first
 * save even when no other hooked screen showed in between. */
uint32_t mt_sw_project_update(void *screen, void *a1, void *a2, void *a3)
{
    settings_tick();
    return ((project_update_fn)mt_sw_next_project_update)(screen, a1, a2, a3);
}

/* 0x60114eaa in the save request 0x60114e34: "bl strncpy(fm + 0x340, name, 0x21)",
 * run only for Save As, after the new path is copied. The next tick sees the new
 * session and keeps the settings for it. */
char *mt_sw_save_name(char *dst, const char *src, uint32_t n)
{
    char *r = ((strncpy_fn)mt_sw_next_save_name)(dst, src, n);
    sw_init_if_needed();
    SW->save_as = project_session();
    return r;
}

/* ---- version text: Config shows 1.2.0-<SWING_VERSION> ----------------------------- */
const char mt_version_fmt[] = "%d.%d.%d-" SWING_VERSION;
