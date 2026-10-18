/*
 * humanize: small random Volume/Velocity and Micro-move FX for the notes in the
 * pattern editor's selection (Polyend Tracker+ firmware 1.2.0).
 *
 * The button. The Pattern screen's bottom bar has three pages (More cycles through
 * them). Page 3 has Pattern Name, Track Name, five unused buttons and More. This patch
 * puts a "Humanize" button on the first unused one (button 2):
 *  - mt_hz_bind replaces the call that binds button 0 on page 3 (0x600e29ba): it binds
 *    button 0 as before, then button 2 to mt_hz_button.
 *  - mt_hz_label / mt_hz_colors / mt_hz_data replace three calls in page 3's label
 *    code (0x600e584c): button 2's text becomes "Humanize", and it gets Track Name's
 *    data and colors, so it looks like an active button.
 * Like the stock editing buttons (Fill, Undo, Redo, page 2), Humanize only works in
 * record mode: without it the button is left unbound and drawn in the grey color set.
 * Toggling record redraws the current page (0x600e2a94 -> pat_show_page), which
 * runs these hooks again.
 *
 * The edit. mt_hz_button does what the stock Invert button does around its edit:
 * push the pattern onto the undo ring (so Undo reverts it), turn the editor's
 * selection into the sequencer's step/track rectangle, edit, rebuild the grid rows
 * and mark the pattern changed (so it gets saved). Without a selection it takes the
 * cursor's whole track, up to the pattern length. With range R, for every step with a
 * note (not empty, note off, cut or fade):
 *  - an existing Volume/Velocity FX gets a random value in a window of R around it,
 *    within 1..100 (an existing V 0 stays 0);
 *  - an existing Micro-move FX the same, within 0..100;
 *  - a missing Micro-move goes into the first free FX slot as m 0..R;
 *  - a missing Volume/Velocity then goes into a free slot, in a window of R around the
 *    default for the step's instrument (samples 0..47, or MIDI and synths 48..66).
 * A window is value - R/2 .. value - R/2 + R; near a limit it slides to stay inside.
 * With Volume/velocity off, V FX are neither changed nor added. Steps without a free
 * slot keep what they have. Random numbers come from the firmware's own generator.
 *
 * The settings. Config > Project Settings gets a "Humanize" folder with Range,
 * Volume/velocity, MIDI velocity and Sample volume. The Config menu is a tree of
 * nodes built at startup by 0x60118acc:
 *  - mt_hz_settings_folder replaces the call that creates Project Settings with room
 *    for 10 items (0x60118b00) and asks for 11;
 *  - mt_hz_last_item replaces the constructor's last item call (Licenses, 0x601193e6),
 *    then adds the Humanize folder and its 4 items, in nodes from the firmware's heap.
 * The values live in HZ (the last 64 bytes of OCRAM). Numbers are stored as list
 * indexes 0..99 and shown + 1, as the stock number editor works. Each named project
 * keeps them in <project folder>/humanize.bin. mt_hz_pattern_update and
 * mt_hz_config_update (the Pattern and Config screens' update methods) load the file
 * when the project changes and save it a short while after a value changes, in the
 * same way colorsong saves its colors. A project without the file gets the defaults;
 * a new project keeps the values it had before its first save.
 */
#include <stdint.h>

#ifndef HUMANIZE_VERSION
#define HUMANIZE_VERSION "hz4"
#endif

/* ---- firmware 1.2.0 addresses (function pointers carry the Thumb bit) ----------- */
#define FW_FM               0x2001E488u     /* file manager object; *FW_FM = FS object */
#define FW_PROJECT_PATH     ((const uint8_t *)(FW_FM + 0x241u))
#define FW_PROJECT_NAME     ((const uint8_t *)(FW_FM + 0x340u))
#define FW_SEQ              0x20017274u     /* sequencer: bytes 0..3 = selection rectangle */
#define FW_SEQ_PATTERN      (FW_SEQ + 0x14u)    /* -> current pattern data (.mtp layout) */
#define FW_PATVIEW          0x20021770u     /* Pattern screen object */
#define FW_ED               (FW_PATVIEW + 0x1000u)  /* its editor state */
#define FW_PROJECT_SETTINGS 0x20028714u     /* Config menu folder "Project Settings" */

#define FW_UNDO_PUSH        0x600C3D91u     /* (fm): copy the current pattern onto the undo ring */
#define FW_SEL_TO_SEQ       0x600E0B59u     /* (): editor selection, or the cursor, -> FW_SEQ[0..3] */
#define FW_FILL_ROWS        0x600DF3A1u     /* (patview): rebuild the grid's row texts */
#define FW_MARK_CHANGED     0x600BCCD9u     /* (fm): mark the current pattern as changed */
#define FW_RAND_RANGE       0x600AF0E9u     /* (lo, hi): lo .. hi-1 (Park-Miller) */
#define FW_BIND_BUTTON      0x600CF6EDu     /* (manager, index, flag, handler) */
#define FW_SET_TEXT         0x60101D5Du     /* disp_ctrl_set_text(display, ctrl, text) */
#define FW_SET_COLORS       0x60101DA5u     /* disp_ctrl_set_colors(display, ctrl, colors) */
#define FW_SET_DATA         0x60101DF9u     /* disp_ctrl_set_data(display, ctrl, data) */
#define FW_MENU_FOLDER      0x60118395u     /* (node, parent, label, capacity) */
#define FW_MENU_ITEM        0x601183E9u     /* (node, parent, label, help, type, descriptor) */
#define FW_MALLOC           0x600A3589u     /* (size) */
#define FW_SNPRINTF         0x6016712Du
#define FW_PATTERN_UPDATE   0x600E1F99u     /* Pattern screen update (vtable slot 0x600403dc) */
#define FW_CONFIG_UPDATE    0x600C6475u     /* Config screen update (vtable slot 0x6003b5f8) */
#define FW_OFF_ON           0x60060B88u     /* option names {"Off", "On"} */

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
#define INSTR_SAMPLE_LAST   47u             /* 0..47 samples, 48..63 MIDI, 64..66 synths */

#define FX_NONE             0u
#define FX_MICRO_MOVE       2u
#define FX_VOLUME           18u

#define HZ_BUTTON           2u              /* page 3, first unused button */

/* FS object methods (offsets in its vtable), as colorsong uses them */
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

typedef void (*void_fn)(void);
typedef void (*obj_fn)(uint32_t obj);
typedef int (*rand_fn)(int lo, int hi);
typedef void (*bind_fn)(void *manager, uint32_t index, uint32_t flag, void_fn handler);
typedef void (*ctrl_fn)(void *display, int ctrl, const void *arg);
typedef void *(*folder_fn)(void *node, void *parent, const char *label, uint32_t capacity);
typedef void *(*item_fn)(void *node, void *parent, const char *label, const char *help,
                         uint32_t type, const void *descriptor);
typedef void *(*malloc_fn)(uint32_t size);
typedef int (*snprintf_fn)(char *buf, uint32_t size, const char *fmt, ...);
typedef void (*update_fn)(void *screen, void *a1, void *a2, void *a3);

void mt_hz_button(void);

/* ---- settings ------------------------------------------------------------------ */

enum { SET_RANGE, SET_VOLUME, SET_MIDI, SET_SAMPLE, SETTINGS };

/* stored values: list indexes 0..99 (shown 1..100) and Off/On (0/1) */
static const uint8_t defaults[SETTINGS] = {10 - 1, 1, 50 - 1, 50 - 1};

#define HZ_BASE             0x20207FC0u     /* last 64 bytes of the 32 KB OCRAM bank */
#define HZ_MAGIC            0x5A48544Du     /* "MTHZ" */
#define SAVE_DELAY          25u             /* screen updates between a change and the save */

struct hz_state {
    uint32_t magic;
    uint32_t session;                       /* project name + path the values belong to */
    uint8_t value[SETTINGS];                /* what the Config menu edits */
    uint8_t saved[SETTINGS];                /* what humanize.bin holds */
    uint8_t saved_valid;                    /* saved[] matches the file on the card */
    uint8_t unnamed;                        /* the session is a project without a name */
    uint8_t wait;                           /* updates left before saving */
    uint8_t reserved;
};
#define HZ ((volatile struct hz_state *)HZ_BASE)
_Static_assert(sizeof(struct hz_state) <= 0x40, "humanize state must fit in its 64 bytes");

void *memset(void *dst, int c, unsigned n);
void *memset(void *dst, int c, unsigned n)
{
    uint8_t *d = dst;
    while (n--)
        *d++ = (uint8_t)c;
    return dst;
}

static void set_defaults(void)
{
    for (uint32_t i = 0; i < SETTINGS; i++)
        HZ->value[i] = defaults[i];
}

static void hz_init_if_needed(void)         /* OCRAM outside .bss holds garbage at power-on */
{
    if (HZ->magic == HZ_MAGIC)
        return;
    set_defaults();
    HZ->session = 0;
    HZ->saved_valid = 0;
    HZ->unnamed = 0;                        /* the first project gets its file or defaults */
    HZ->wait = 0;
    HZ->magic = HZ_MAGIC;
}

static uint32_t setting(uint32_t i)
{
    hz_init_if_needed();
    uint32_t v = HZ->value[i];
    if (i == SET_VOLUME)
        return v != 0;
    return (v > 99 ? 99 : v) + 1;           /* 1..100 */
}

/* ---- the edit ----------------------------------------------------------------- */

static int rnd(int lo, int hi)                  /* lo .. hi, inclusive */
{
    return ((rand_fn)FW_RAND_RANGE)(lo, hi + 1);
}

/* A random value in the window of `range` around `center`, slid to stay in lo..hi. */
static uint8_t pick(int center, int range, int lo, int hi)
{
    int a = center - range / 2, b = a + range;
    if (b - a >= hi - lo) {
        a = lo;
        b = hi;
    } else if (a < lo) {
        b += lo - a;
        a = lo;
    } else if (b > hi) {
        a -= b - hi;
        b = hi;
    }
    return (uint8_t)rnd(a, b);
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

struct hz_params { int range, volume, midi, sample; };

static void humanize_step(uint8_t *step, const struct hz_params *p)
{
    if ((int8_t)step[0] < 0)                    /* empty (-1), or note off / cut / fade */
        return;
    int has_micro = 0, has_volume = 0;
    for (uint32_t k = 0; k < 2; k++) {
        uint8_t *fx = step + 2 + 2 * k;
        if (fx[0] == FX_VOLUME) {
            has_volume = 1;
            if (p->volume && fx[1] != 0)        /* V 0 is a deliberate silent note */
                fx[1] = pick(fx[1], p->range, 1, 100);
        } else if (fx[0] == FX_MICRO_MOVE) {
            has_micro = 1;
            fx[1] = pick(fx[1], p->range, 0, 100);
        }
    }
    if (!has_micro)
        add_fx(step, FX_MICRO_MOVE, (uint8_t)rnd(0, p->range));
    if (p->volume && !has_volume) {
        int base = step[1] <= INSTR_SAMPLE_LAST ? p->sample : p->midi;
        add_fx(step, FX_VOLUME, pick(base, p->range, 1, 100));
    }
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

    struct hz_params p = {(int)setting(SET_RANGE), (int)setting(SET_VOLUME),
                          (int)setting(SET_MIDI), (int)setting(SET_SAMPLE)};
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
            humanize_step(data + PAT_STEPS + PAT_TRACK_SIZE * t + 6 * s, &p);

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

/* ---- the Config menu ------------------------------------------------------------ */

#define MENU_FOLDER_SIZE    0x20u           /* fields up to +0x1e */
#define MENU_ITEM_SIZE      0x20u           /* fields up to +0x18 */
#define MENU_NUMBER         2u              /* stock number item (e.g. Metronome Volume) */
#define MENU_OPTIONS        4u              /* stock option item (e.g. Anti-aliasing) */

/* number item: value pointer, then min, max, step and display offset */
struct menu_number { volatile uint8_t *value; uint8_t min, max, step, offset; };
/* option item (type 4): value pointer, option count, option names, and a callback that
 * the menu's apply (cfg_menu_apply, 0x60118658) calls after storing the choice, without
 * checking it for NULL. The stock ones apply the setting (Metronome State's is empty). */
struct menu_options { volatile uint8_t *value; uint32_t count; const char *const *names; void_fn on_apply; };

static void option_applied(void)            /* settings_tick sees the new value */
{
}

static const struct menu_number range_desc = {&HZ->value[SET_RANGE], 0, 99, 1, 1};
static const struct menu_options volume_desc = {&HZ->value[SET_VOLUME], 2, (const char *const *)FW_OFF_ON,
                                                option_applied};
static const struct menu_number midi_desc = {&HZ->value[SET_MIDI], 0, 99, 1, 1};
static const struct menu_number sample_desc = {&HZ->value[SET_SAMPLE], 0, 99, 1, 1};

/* replaces "bl 0x60118394" at 0x60118b00: Project Settings, 10 items -> 11 */
void *mt_hz_settings_folder(void *node, void *parent, const char *label, uint32_t capacity)
{
    return ((folder_fn)FW_MENU_FOLDER)(node, parent, label, capacity + 1);
}

/* replaces the constructor's last "bl 0x601183e8" (Licenses) at 0x601193e6 */
void *mt_hz_last_item(void *node, void *parent, const char *label, const char *help,
                      uint32_t type, const void *descriptor)
{
    void *r = ((item_fn)FW_MENU_ITEM)(node, parent, label, help, type, descriptor);
    hz_init_if_needed();
    malloc_fn alloc = (malloc_fn)FW_MALLOC;
    void *folder = alloc(MENU_FOLDER_SIZE);
    if (!folder)
        return r;
    ((folder_fn)FW_MENU_FOLDER)(folder, (void *)FW_PROJECT_SETTINGS, "Humanize", SETTINGS);
    static const struct { const char *label, *help; uint32_t type; const void *desc; } items[SETTINGS] = {
        {"Range", "How far Humanize moves timing and level. New Micro-move: 0 to this "
                  "value. Volume/Velocity and existing Micro-move: a random value in a "
                  "window this wide.", MENU_NUMBER, &range_desc},
        {"Volume/velocity", "Off: Humanize changes only Micro-move and leaves "
                            "Volume/Velocity alone.", MENU_OPTIONS, &volume_desc},
        {"MIDI velocity", "Center for a new Volume/Velocity FX on MIDI and synth "
                          "instruments.", MENU_NUMBER, &midi_desc},
        {"Sample volume", "Center for a new Volume/Velocity FX on sample instruments.",
         MENU_NUMBER, &sample_desc},
    };
    for (uint32_t i = 0; i < SETTINGS; i++) {
        void *item = alloc(MENU_ITEM_SIZE);
        if (item)
            ((item_fn)FW_MENU_ITEM)(item, folder, items[i].label, items[i].help,
                                    items[i].type, items[i].desc);
    }
    return r;
}

/* ---- humanize.bin ---------------------------------------------------------------- */

#define FILE_MAGIC          0x315A4848u     /* "HHZ1" */
#define FILE_SIZE           12u             /* magic, 4 values, 4-byte checksum */

static uint32_t fnv1a(uint32_t h, uint32_t byte)
{
    return (h ^ byte) * 16777619u;
}

static uint32_t project_session(void)
{
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < 64 && FW_PROJECT_NAME[i]; i++)
        h = fnv1a(h, FW_PROJECT_NAME[i]);
    h = fnv1a(h, 0);
    for (uint32_t i = 0; i < 255 && FW_PROJECT_PATH[i]; i++)
        h = fnv1a(h, FW_PROJECT_PATH[i]);
    return fnv1a(h, 0) | 1;                 /* never 0 */
}

static uint32_t values_sum(const uint8_t *v)
{
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < SETTINGS; i++)
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
    if (!FW_PROJECT_NAME[0])
        return 0;
    int n = ((snprintf_fn)FW_SNPRINTF)(path, size, "%s/%s/humanize.bin",
                                       (const char *)FW_PROJECT_PATH, (const char *)FW_PROJECT_NAME);
    return n > 0 && (uint32_t)n < size;
}

static int settings_valid(const uint8_t *v)
{
    return v[SET_RANGE] <= 99 && v[SET_VOLUME] <= 1 && v[SET_MIDI] <= 99 && v[SET_SAMPLE] <= 99;
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
    ok = ok && get32(buf) == FILE_MAGIC && get32(buf + 8) == values_sum(buf + 4)
         && settings_valid(buf + 4);
    if (!ok)
        return 0;
    for (uint32_t i = 0; i < SETTINGS; i++)
        HZ->value[i] = HZ->saved[i] = buf[4 + i];
    HZ->saved_valid = 1;
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
    put32(buf, FILE_MAGIC);
    for (uint32_t i = 0; i < SETTINGS; i++)
        buf[4 + i] = HZ->value[i];
    put32(buf + 8, values_sum(buf + 4));
    struct fs_open_result res = {0, 0, {0, 0, 0}};
    ((fs_remove_fn)vt[FS_REMOVE / 4])(fs, path);
    ((fs_open_fn)vt[FS_OPEN / 4])(&res, fs, path, FS_MODE_WRITE, 0);
    if (!res.ok)
        return;
    int ok = ((fs_write_fn)vt[FS_WRITE / 4])(fs, buf, FILE_SIZE, res.handle) == (int)FILE_SIZE;
    ((fs_close_fn)vt[FS_CLOSE / 4])(fs, res.handle, 1);
    if (ok) {
        for (uint32_t i = 0; i < SETTINGS; i++)
            HZ->saved[i] = buf[4 + i];
        HZ->saved_valid = 1;
    }
}

static int values_changed(void)
{
    if (!HZ->saved_valid)
        return 1;
    for (uint32_t i = 0; i < SETTINGS; i++)
        if (HZ->value[i] != HZ->saved[i])
            return 1;
    return 0;
}

/* Called from the Pattern and Config screens' update methods (where the stock code
 * does its own file I/O). At most one small file read or write per call. */
static void settings_tick(void)
{
    hz_init_if_needed();
    if (!*(void **)FW_FM)
        return;
    uint32_t session = project_session();
    int unnamed = !FW_PROJECT_NAME[0];
    if (session != HZ->session) {
        int keep = HZ->unnamed;             /* a new project being saved for the first time */
        HZ->session = session;
        HZ->unnamed = (uint8_t)unnamed;
        HZ->saved_valid = 0;
        HZ->wait = SAVE_DELAY;
        if (unnamed) {
            if (!keep)
                set_defaults();             /* a new, empty project */
        } else if (!settings_load()) {
            if (!keep) {
                set_defaults();             /* a project without settings of its own */
                for (uint32_t i = 0; i < SETTINGS; i++)
                    HZ->saved[i] = HZ->value[i];
                HZ->saved_valid = 1;        /* nothing to write until a value changes */
            }
        }
        return;
    }
    if (unnamed || !values_changed()) {
        HZ->wait = SAVE_DELAY;
        return;
    }
    if (HZ->wait) {
        HZ->wait--;
        return;
    }
    HZ->wait = SAVE_DELAY;
    settings_save();
}

/* Pattern screen vtable slot 0x600403dc (update method 0x600e1f99) */
void mt_hz_pattern_update(void *screen, void *a1, void *a2, void *a3)
{
    settings_tick();
    ((update_fn)FW_PATTERN_UPDATE)(screen, a1, a2, a3);
}

/* Config screen vtable slot 0x6003b5f8 (update method 0x600c6475) */
void mt_hz_config_update(void *screen, void *a1, void *a2, void *a3)
{
    settings_tick();
    ((update_fn)FW_CONFIG_UPDATE)(screen, a1, a2, a3);
}

/* ---- version text: Config shows 1.2.0-<HUMANIZE_VERSION> ------------------------ */
const char mt_version_fmt[] = "%d.%d.%d-" HUMANIZE_VERSION;
