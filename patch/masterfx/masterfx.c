/*
 * masterfx: a choice of master reverb per project (Polyend Tracker+ firmware 1.2.0).
 *
 * Project Settings gets a "Master FX" folder with a "Reverb" item: Stock (the default)
 * or Freeverb. Each named project keeps its choice in <project folder>/masterfx.bin;
 * a project without the file, and every project in stock firmware, plays the stock
 * reverb.
 *
 * The reverb. The master reverb is one audio object (0x20019014) in the firmware's
 * Teensy-style audio graph; once per audio block its update() (vtable 0x60042f94,
 * 0x60031e5d) processes the stereo send in place and transmits it as the return.
 * mt_mfx_reverb_update replaces that update(): with Stock chosen it calls the stock
 * update(), so the stock reverb runs exactly as without the patch; with Freeverb it
 * runs Freeverb (freeverb_dsp.h) on the same blocks, with the same
 * Master Reverb knobs.
 *
 * Switching. Both effects keep their delay lines in the reverb object's two 32 KB
 * buffers (object +0x24, +0x28), so a switch clears them first, a piece per block over
 * 8 blocks (about 23 ms with no reverb return; the dry signal is not touched): the
 * stock reverb would otherwise play back Freeverb's lines, and Freeverb the stock
 * reverb's. Switching to the stock reverb also zeroes its three filter states (+0x60,
 * +0x64, +0x68); the rest of what it keeps (positions, LFO) carries on. A switch
 * happens when the choice changes in the menu, or when a project with a different
 * choice is loaded.
 *
 * Start-up. Until the Pattern screen's update method first runs, the stock reverb runs
 * whatever the project chose: running Freeverb while the unit boots hangs the boot
 * (docs/FINDINGS.md, section 19). The Config menu constructor (a static constructor, so
 * at every start) puts it back to the stock reverb until then.
 *
 * Shared hooks. The Project Settings folder, the menu constructor's last item and the
 * Pattern and Config screens' update methods are hooked through chain words
 * (mt_mfx_next_*) that tools/patch_ptf.py fills with what the site pointed to before,
 * so other patches can hook them too. The reverb's update() slot is this patch's alone.
 */
#include <stdint.h>

#ifndef MASTERFX_VERSION
#define MASTERFX_VERSION "mf1"
#endif

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_mfx_next_folder);             /* Project Settings: cfg_menu_folder 0x60118395 */
CHAIN_WORD(mt_mfx_next_last_item);          /* last item: cfg_menu_item 0x601183e9 */
CHAIN_WORD(mt_mfx_next_pattern_update);     /* Pattern screen update 0x600e1f99 */
CHAIN_WORD(mt_mfx_next_config_update);      /* Config screen update 0x600c6475 */

#include "freeverb_dsp.h"

/* ---- firmware 1.2.0 addresses (function pointers carry the Thumb bit) ----------- */
#define FW_FM               0x2001E488u     /* file manager object; *FW_FM = FS object */
#define FW_PROJECT_PATH     ((const uint8_t *)(FW_FM + 0x241u))
#define FW_PROJECT_NAME     ((const uint8_t *)(FW_FM + 0x340u))
#define FW_PROJECT_SETTINGS 0x20028714u     /* Config menu folder "Project Settings" */
#define FW_MENU_FOLDER      0x60118395u     /* (node, parent, label, capacity) */
#define FW_MENU_ITEM        0x601183E9u     /* (node, parent, label, help, type, descriptor) */
#define FW_MALLOC           0x600A3589u     /* (size) */
#define FW_SNPRINTF         0x6016712Du

/* stock reverb filter states, zeroed when switching to it */
#define RV_FILTER_FIRST     0x60u
#define RV_FILTER_LAST      0x68u

typedef void (*void_fn)(void);
typedef void (*update_fn)(void *obj);
typedef void (*screen_update_fn)(void *screen, void *a1, void *a2, void *a3);
typedef void *(*folder_fn)(void *node, void *parent, const char *label, uint32_t capacity);
typedef void *(*item_fn)(void *node, void *parent, const char *label, const char *help,
                         uint32_t type, const void *descriptor);
typedef void *(*malloc_fn)(uint32_t size);
typedef int (*snprintf_fn)(char *buf, uint32_t size, const char *fmt, ...);

/* ---- state (.mt_ram: neither loaded nor zeroed; the patcher places it) ----------- */

#define REVERB_STOCK        0u
#define REVERB_FREEVERB     1u
#define REVERBS             2u

#define MX_MAGIC            0x5846464Du     /* "MFFX": the settings fields are valid */
#define MX_READY            0x59444552u     /* "REDY": booted, a swapped reverb may run */
#define SAVE_DELAY          25u             /* screen updates between a change and the save */

struct mfx_state {
    uint32_t magic;
    uint32_t session;                       /* project name + path the choice belongs to */
    uint8_t reverb;                         /* the choice (the menu edits it): REVERB_* */
    uint8_t saved;                          /* what masterfx.bin holds for it */
    uint8_t saved_valid;
    uint8_t unnamed;                        /* the session is a project without a name */
    uint8_t wait;                           /* updates left before saving */
    uint8_t delay;                          /* masterfx.bin's delay byte, kept as read (0) */
    uint8_t pad[2];
    /* the audio side (reset at every start) */
    uint32_t ready;                         /* MX_READY once the unit has booted */
    uint8_t active;                         /* the reverb the hook runs: REVERB_* */
    uint8_t switching;                      /* clearing the buffers for `target` */
    uint8_t target;
    uint8_t pad2;
};
static struct mfx_state mfx_state_ram __attribute__((section(".mt_ram")));
#define MX (&mfx_state_ram)

static void mx_init_if_needed(void)         /* RAM outside .bss holds garbage at power-on */
{
    if (MX->magic == MX_MAGIC)
        return;
    MX->reverb = REVERB_STOCK;
    MX->session = 0;
    MX->saved_valid = 0;
    MX->unnamed = 0;                        /* the first project gets its file or the default */
    MX->wait = 0;
    MX->delay = 0;
    MX->magic = MX_MAGIC;
}

/* ---- the reverb (audio interrupt) ------------------------------------------------ */

/* Start clearing the buffers for a switch to `target`. */
static void switch_to(uint32_t target, uint32_t buf_l, uint32_t buf_r)
{
    if (target == REVERB_FREEVERB) {
        fv_init(buf_l, buf_r);              /* lays out the lines, cleared = 0 */
    } else {
        FV->magic = 0;                      /* Freeverb lays out its lines again next time */
        FV->buf_l = buf_l;
        FV->buf_r = buf_r;
        FV->cleared = 0;
    }
    MX->target = (uint8_t)target;
    MX->switching = 1;
}

/* Reverb object vtable slot 0x60042f94 (update method 0x60031e5d) */
void mt_mfx_reverb_update(uint8_t *obj)
{
    if (MX->ready != MX_READY || MX->magic != MX_MAGIC) {   /* still booting: the stock reverb */
        ((update_fn)FW_REVERB_UPDATE)(obj);
        return;
    }
    uint32_t buf_l = *(volatile uint32_t *)(obj + RV_BUF_L), buf_r = *(volatile uint32_t *)(obj + RV_BUF_R);
    uint32_t want = *(volatile uint8_t *)&MX->reverb;
    if (want >= REVERBS)
        want = REVERB_STOCK;
    if (!MX->switching && want != MX->active && buf_l && buf_r)
        switch_to(want, buf_l, buf_r);
    if (MX->switching) {
        if (!buf_l || !buf_r)
            return;
        if (FV->buf_l != buf_l || FV->buf_r != buf_r)
            switch_to(MX->target, buf_l, buf_r);    /* the buffers moved: start again */
        if (!clear_step(CLEAR_WORDS))       /* clearing: silent, like the bypass */
            return;
        if (MX->target == REVERB_STOCK)
            for (uint32_t off = RV_FILTER_FIRST; off <= RV_FILTER_LAST; off += 4)
                *(volatile uint32_t *)(obj + off) = 0;
        MX->active = MX->target;
        MX->switching = 0;
        return;
    }
    if (MX->active != REVERB_FREEVERB) {
        ((update_fn)FW_REVERB_UPDATE)(obj);
        return;
    }
    if (obj[RV_BYPASS] || !buf_l || !buf_r)
        return;
    if (FV->magic != FV_MAGIC || FV->buf_l != buf_l || FV->buf_r != buf_r) {
        switch_to(REVERB_FREEVERB, buf_l, buf_r);   /* the buffers moved: lay out and clear */
        return;
    }
    freeverb_blocks(obj, 0);
}

/* ---- the Config menu ------------------------------------------------------------ */

#define MENU_ITEM_SIZE      0x20u           /* fields up to +0x18 */
#define MENU_FOLDER_SIZE    0x20u           /* fields up to +0x1e */
#define MENU_OPTIONS        4u              /* stock option item (e.g. Anti-aliasing) */
#define FOLDER_ITEMS        1u              /* Reverb */

/* option item (type 4): value pointer, option count, option names, and a callback that
 * the menu's apply (cfg_menu_apply, 0x60118658) calls after storing the choice, without
 * checking it for NULL. */
struct menu_options { volatile uint8_t *value; uint32_t count; const char *const *names; void_fn on_apply; };

static void option_applied(void)            /* the reverb hook and settings_tick see the value */
{
}

static const char *const reverb_names[REVERBS] = {"Stock", "Freeverb"};
static const struct menu_options reverb_desc = {&MX->reverb, REVERBS, reverb_names, option_applied};

/* replaces "bl 0x60118394" at 0x60118b00 (chained): Project Settings gets room for the
 * Master FX folder */
void *mt_mfx_settings_folder(void *node, void *parent, const char *label, uint32_t capacity)
{
    return ((folder_fn)mt_mfx_next_folder)(node, parent, label, capacity + 1);
}

/* replaces the constructor's last "bl 0x601183e8" (Licenses) at 0x601193e6 (chained):
 * a static constructor, so this runs at every start, before the audio */
void *mt_mfx_last_item(void *node, void *parent, const char *label, const char *help,
                       uint32_t type, const void *descriptor)
{
    MX->ready = 0;                          /* the stock reverb until the Pattern screen runs */
    MX->active = REVERB_STOCK;
    MX->switching = 0;
    FV->magic = 0;
    void *r = ((item_fn)mt_mfx_next_last_item)(node, parent, label, help, type, descriptor);
    mx_init_if_needed();
    malloc_fn alloc = (malloc_fn)FW_MALLOC;
    void *folder = alloc(MENU_FOLDER_SIZE);
    if (folder) {
        ((folder_fn)FW_MENU_FOLDER)(folder, (void *)FW_PROJECT_SETTINGS, "Master FX", FOLDER_ITEMS);
        void *item = alloc(MENU_ITEM_SIZE);
        if (item)
            ((item_fn)FW_MENU_ITEM)(item, folder, "Reverb",
                                    "The master reverb for this project: Stock (the Tracker's own) "
                                    "or Freeverb. The Master Reverb knobs work for both.",
                                    MENU_OPTIONS, &reverb_desc);
    }
    return r;
}

/* ---- masterfx.bin ---------------------------------------------------------------- */

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

#define FILE_MAGIC          0x31464D48u     /* "HMF1" */
#define FILE_SIZE           12u             /* magic, reverb, delay, 2 zero bytes, 4-byte checksum */

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
    if (!FW_PROJECT_NAME[0])
        return 0;
    int n = ((snprintf_fn)FW_SNPRINTF)(path, size, "%s/%s/masterfx.bin",
                                       (const char *)FW_PROJECT_PATH, (const char *)FW_PROJECT_NAME);
    return n > 0 && (uint32_t)n < size;
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
    ok = ok && get32(buf) == FILE_MAGIC && get32(buf + 8) == data_sum(buf + 4) && buf[4] < REVERBS;
    if (!ok)
        return 0;
    MX->reverb = MX->saved = buf[4];
    MX->delay = buf[5];
    MX->saved_valid = 1;
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
    uint8_t reverb = MX->reverb;
    put32(buf, FILE_MAGIC);
    buf[4] = reverb;
    buf[5] = MX->delay;
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
        MX->saved = reverb;
        MX->saved_valid = 1;
    }
}

/* Called from the Pattern and Config screens' update methods (where the stock code
 * does its own file I/O). At most one small file read or write per call. */
static void settings_tick(void)
{
    mx_init_if_needed();
    if (!*(void **)FW_FM)
        return;
    uint32_t session = project_session();
    int unnamed = !FW_PROJECT_NAME[0];
    if (session != MX->session) {
        int keep = MX->unnamed;             /* a new project being saved for the first time */
        MX->session = session;
        MX->unnamed = (uint8_t)unnamed;
        MX->saved_valid = 0;
        MX->wait = SAVE_DELAY;
        if (unnamed) {
            if (!keep) {
                MX->reverb = REVERB_STOCK;  /* a new, empty project */
                MX->delay = 0;
            }
        } else if (!settings_load()) {
            if (!keep) {
                MX->reverb = REVERB_STOCK;  /* a project without the file */
                MX->delay = 0;
                MX->saved = REVERB_STOCK;
                MX->saved_valid = 1;        /* nothing to write until the choice changes */
            }
        }
        return;
    }
    if (MX->reverb >= REVERBS)
        MX->reverb = REVERB_STOCK;
    if (unnamed || (MX->saved_valid && MX->reverb == MX->saved)) {
        MX->wait = SAVE_DELAY;
        return;
    }
    if (MX->wait) {
        MX->wait--;
        return;
    }
    MX->wait = SAVE_DELAY;
    settings_save();
}

/* Pattern screen vtable slot 0x600403dc (update method 0x600e1f99; chained): the
 * project's choice, and the unit has booted, so a swapped reverb may run */
void mt_mfx_pattern_update(void *screen, void *a1, void *a2, void *a3)
{
    settings_tick();
    MX->ready = MX_READY;
    ((screen_update_fn)mt_mfx_next_pattern_update)(screen, a1, a2, a3);
}

/* Config screen vtable slot 0x6003b5f8 (update method 0x600c6475; chained) */
void mt_mfx_config_update(void *screen, void *a1, void *a2, void *a3)
{
    settings_tick();
    ((screen_update_fn)mt_mfx_next_config_update)(screen, a1, a2, a3);
}

/* ---- version text: Config shows 1.2.0-<MASTERFX_VERSION> ------------------------ */
const char mt_version_fmt[] = "%d.%d.%d-" MASTERFX_VERSION;
