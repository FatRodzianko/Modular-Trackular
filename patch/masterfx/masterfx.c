/*
 * masterfx: a choice of master reverb and delay per project (Polyend Tracker+ firmware
 * 1.2.0).
 *
 * Project Settings gets a "Master FX" folder with two items: "Reverb", Stock (the
 * default), Freeverb, Plate, Spring, Shimmer, "Gated" or Reverse (the gated burst,
 * swelling instead of cutting in), and "Delay", Stock (the default), Tape or BBD.
 * "Gated" sat in the Delay list before mf7; in the Reverb list it and Shimmer, the two
 * heaviest effects, can no longer run at once, which caps the master-FX worst case
 * (docs/MASTERFX_EFFICIENCY_PLAN.md section 9).
 * Each saved project keeps its choices in <project folder>/masterfx.bin; a project
 * without the file, a new project ("New Project", no path: nothing written until it
 * has a folder), and every project in stock firmware, play the stock reverb and delay.
 * Save As (a new project's first save is one; marked at the save request's name copy,
 * 0x60114eaa) keeps the choices for the new name and writes them there, as the
 * firmware copies only its own files. The file is read and written only while the
 * file manager is idle.
 *
 * The reverb. The master reverb is one audio object (0x20019014) in the firmware's
 * Teensy-style audio graph; once per audio block its update() (vtable 0x60042f94,
 * 0x60031e5d) processes the stereo send in place and transmits it as the return.
 * mt_mfx_reverb_update replaces that update(): with Stock chosen it calls the stock
 * update(), so the stock reverb runs exactly as without the patch; otherwise it runs the
 * chosen reverb (freeverb_dsp.h, plate_dsp.h, spring_dsp.h, shimmer_dsp.h,
 * gated_dsp.h) on the same blocks, with the same Master Reverb knobs.
 *
 * Switching. All the reverbs keep their delay lines in the reverb object's two 32 KB
 * buffers (object +0x24, +0x28), so a switch clears them first, a piece per block over
 * 8 blocks (about 23 ms with no reverb return; the dry signal is not touched): each
 * reverb would otherwise play back what another left there. Switching to the stock reverb also zeroes its three filter states (+0x60,
 * +0x64, +0x68); the rest of what it keeps (positions, LFO) carries on. A switch
 * happens when the choice changes in the menu, or when a project with a different
 * choice is loaded.
 *
 * The delay. The master delay (0x20018fc0) works the same way: its update() (vtable
 * 0x60042fec, 0x60032405) takes the mono delay send and transmits a stereo return.
 * mt_mfx_delay_update calls the stock update() with Stock chosen, and runs the tape
 * echo (tape_dsp.h) or the bucket-brigade delay (bbd_dsp.h) otherwise. They live in the
 * stock delay's own buffers (three of 512 KB at
 * object +0x14, +0x18, +0x1c), so a switch to either clears the two it uses and
 * a switch to Stock clears all three and zeroes the stock delay's filter states
 * (+0x3c..+0x50), 32 KB per block (about 0.1 s, then 0.14 s, with no delay return).
 *
 * Start-up. Until the Pattern screen's update method first runs, the stock reverb and
 * delay run whatever the project chose: running Freeverb while the unit boots hangs the
 * boot (docs/FINDINGS.md, section 19), and the tape is held to the same rule. The Config
 * menu constructor (a static constructor, so at every start) puts both back to stock
 * until then.
 *
 * Shared hooks. The Project Settings folder, the menu constructor's last item and the
 * Pattern and Config screens' update methods are hooked through chain words
 * (mt_mfx_next_*) that tools/patch_ptf.py fills with what the site pointed to before,
 * so other patches can hook them too. The reverb's and the delay's update() slots are
 * this patch's alone.
 */
#include <stdint.h>

#ifndef MASTERFX_VERSION
#define MASTERFX_VERSION "mf8"
#endif

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_mfx_next_folder);             /* Project Settings: cfg_menu_folder 0x60118395 */
CHAIN_WORD(mt_mfx_next_last_item);          /* last item: cfg_menu_item 0x601183e9 */
CHAIN_WORD(mt_mfx_next_pattern_update);     /* Pattern screen update 0x600e1f99 */
CHAIN_WORD(mt_mfx_next_config_update);      /* Config screen update 0x600c6475 */
CHAIN_WORD(mt_mfx_next_save_name);          /* strncpy 0x601673d7 (Save As: the new name) */
CHAIN_WORD(mt_mfx_next_project_update);     /* project screen update 0x600ead59 */

#include "freeverb_dsp.h"
#include "shimmer_dsp.h"
#include "plate_dsp.h"
#include "spring_dsp.h"
#include "tape_dsp.h"
#include "bbd_dsp.h"
#include "gated_dsp.h"

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

/* stock reverb filter states, zeroed when switching to it */
#define RV_FILTER_FIRST     0x60u
#define RV_FILTER_LAST      0x68u

typedef void (*void_fn)(void);
typedef void (*update_fn)(void *obj);
typedef void (*screen_update_fn)(void *screen, void *a1, void *a2, void *a3);
typedef uint32_t (*project_update_fn)(void *screen, void *a1, void *a2, void *a3);
typedef void *(*folder_fn)(void *node, void *parent, const char *label, uint32_t capacity);
typedef void *(*item_fn)(void *node, void *parent, const char *label, const char *help,
                         uint32_t type, const void *descriptor);
typedef void *(*malloc_fn)(uint32_t size);
typedef int (*snprintf_fn)(char *buf, uint32_t size, const char *fmt, ...);
typedef char *(*strncpy_fn)(char *dst, const char *src, uint32_t n);

/* ---- state (.mt_ram: neither loaded nor zeroed; the patcher places it) ----------- */

#define REVERB_STOCK        0u
#define REVERB_FREEVERB     1u
#define REVERB_PLATE        2u
#define REVERB_SPRING       3u
#define REVERB_SHIMMER      4u
#define REVERB_GATED        5u
#define REVERB_REVERSE      6u              /* the same burst, reversed */
#define REVERBS             7u
#define DELAY_STOCK         0u
#define DELAY_TAPE          1u
#define DELAY_BBD           2u
#define DELAYS              3u
#define OLD_DELAYS          4u              /* older files: 3 was "Gated", now in the reverbs */
#define DL_CLEAR_WORDS      8192u           /* words of the delay buffers cleared per block (32 KB) */
#define DL_BUFFERS          3u
#define DL_BUFFER_WORDS     (TAPE_LEN / 2u)

#define MX_MAGIC            0x3558464Du     /* "MFX5": the settings fields are valid (mf5's layout) */
#define MX_READY            0x59444552u     /* "REDY": booted, a swapped reverb may run */
#define SAVE_DELAY          25u             /* screen updates between a change and the save */

struct mfx_state {
    uint32_t magic;
    uint32_t session;                       /* project name + path the choice belongs to */
    uint8_t reverb;                         /* the choice (the menu edits it): REVERB_* */
    uint8_t saved;                          /* what masterfx.bin holds for it */
    uint8_t saved_valid;
    uint8_t load;                           /* the project's file is still to be read */
    uint8_t wait;                           /* updates left before saving */
    uint8_t delay;                         /* the delay choice (the menu edits it): DELAY_* */
    uint8_t saved_delay;                    /* what masterfx.bin holds for it */
    uint8_t pad;
    /* the audio side (reset at every start) */
    uint32_t ready;                         /* MX_READY once the unit has booted */
    uint8_t active;                         /* the reverb the hook runs: REVERB_* */
    uint8_t switching;                      /* clearing the buffers for `target` */
    uint8_t target;
    uint8_t pad2;
    uint32_t rv_cleared;                    /* words of each reverb buffer cleared so far */
    uint32_t rv_buf[2];                     /* the reverb buffers being cleared */
    uint8_t dl_active;                      /* the delay the hook runs: DELAY_* */
    uint8_t dl_switching;                   /* clearing the delay buffers for dl_target */
    uint8_t dl_target;
    uint8_t pad3;
    uint32_t dl_cleared;                    /* words of the delay buffers cleared so far */
    uint32_t dl_buf[DL_BUFFERS];            /* the buffers being cleared */
    /* the settings again, last, so the audio side stays where the tests expect it */
    uint32_t save_as;                       /* the session a Save As moves the choices to; 0: none */
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
    MX->load = 0;
    MX->wait = 0;
    MX->save_as = 0;
    MX->delay = DELAY_STOCK;
    MX->magic = MX_MAGIC;
}

/* ---- the reverb (audio interrupt) ------------------------------------------------ */

/* Start clearing the reverb buffers for a switch to `target`. */
static void switch_to(uint32_t target, uint32_t buf_l, uint32_t buf_r)
{
    FV->magic = 0;                          /* each reverb lays out its lines again */
    PL->magic = 0;
    SP->magic = 0;
    SH->magic = 0;
    GT->magic = 0;
    MX->rv_buf[0] = buf_l;
    MX->rv_buf[1] = buf_r;
    MX->rv_cleared = 0;
    MX->target = (uint8_t)target;
    MX->switching = 1;
}

/* Clear the next piece of both reverb buffers; 1 when they are clear. */
static int rv_clear_step(void)
{
    volatile uint32_t *l = (volatile uint32_t *)MX->rv_buf[0], *r = (volatile uint32_t *)MX->rv_buf[1];
    uint32_t end = MX->rv_cleared + CLEAR_WORDS;
    if (end > BUFFER_SAMPLES / 2)
        end = BUFFER_SAMPLES / 2;
    for (uint32_t i = MX->rv_cleared; i < end; i++) {
        l[i] = 0;
        r[i] = 0;
    }
    MX->rv_cleared = end;
    return end == BUFFER_SAMPLES / 2;
}

/* The chosen reverb's lines are laid out in these buffers. */
static int rv_ready(uint32_t which, uint32_t buf_l, uint32_t buf_r)
{
    if (which == REVERB_FREEVERB)
        return FV->magic == FV_MAGIC && FV->buf_l == buf_l && FV->buf_r == buf_r;
    if (which == REVERB_PLATE)
        return PL->magic == PL_MAGIC && PL->buf_l == buf_l && PL->buf_r == buf_r;
    if (which == REVERB_SPRING)
        return SP->magic == SP_MAGIC && SP->buf_l == buf_l && SP->buf_r == buf_r;
    if (which == REVERB_SHIMMER)
        return SH->magic == SH_MAGIC && SH->buf_l == buf_l && SH->buf_r == buf_r;
    if (which == REVERB_GATED || which == REVERB_REVERSE)
        return GT->magic == GT_MAGIC && GT->buf[0] == buf_l && GT->buf[1] == buf_r;
    return 0;
}

/* Lay out the chosen reverb in the (cleared) buffers. */
static void rv_init(uint8_t *obj, uint32_t which, uint32_t buf_l, uint32_t buf_r)
{
    if (which == REVERB_FREEVERB) {
        fv_init(buf_l, buf_r);
        FV->cleared = BUFFER_SAMPLES / 2;
    } else if (which == REVERB_PLATE) {
        plate_init(buf_l, buf_r);
    } else if (which == REVERB_SPRING) {
        spring_init(buf_l, buf_r);
    } else if (which == REVERB_SHIMMER) {
        shimmer_init(buf_l, buf_r);
    } else if (which == REVERB_GATED || which == REVERB_REVERSE) {
        gated_init(buf_l, buf_r);
    } else {
        for (uint32_t off = RV_FILTER_FIRST; off <= RV_FILTER_LAST; off += 4)
            *(volatile uint32_t *)(obj + off) = 0;  /* the stock reverb's filter states */
    }
}

static void rv_blocks(uint8_t *obj, uint32_t which)
{
    if (which == REVERB_FREEVERB)
        freeverb_blocks(obj, 0);
    else if (which == REVERB_PLATE)
        plate_blocks(obj);
    else if (which == REVERB_SPRING)
        spring_blocks(obj);
    else if (which == REVERB_SHIMMER)
        shimmer_blocks(obj);
    else if (which == REVERB_GATED || which == REVERB_REVERSE)
        gated_blocks(obj, which == REVERB_REVERSE);
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
        if (MX->rv_buf[0] != buf_l || MX->rv_buf[1] != buf_r)
            switch_to(MX->target, buf_l, buf_r);    /* the buffers moved: start again */
        if (!rv_clear_step())               /* clearing: silent, like the bypass */
            return;
        rv_init(obj, MX->target, buf_l, buf_r);
        MX->active = MX->target;
        MX->switching = 0;
        return;
    }
    if (MX->active == REVERB_STOCK) {
        ((update_fn)FW_REVERB_UPDATE)(obj);
        return;
    }
    if (obj[RV_BYPASS] || !buf_l || !buf_r)
        return;
    if (!rv_ready(MX->active, buf_l, buf_r)) {
        switch_to(MX->active, buf_l, buf_r);    /* the buffers moved: clear and lay out again */
        return;
    }
    rv_blocks(obj, MX->active);
}

/* ---- the delay (audio interrupt) ------------------------------------------------- */

/* Start clearing the delay buffers for a switch to `target`: the two lines for Tape or BBD, all
 * three for the stock delay. */
static void dl_switch_to(uint32_t target, const uint32_t *buf)
{
    for (uint32_t i = 0; i < DL_BUFFERS; i++)
        MX->dl_buf[i] = buf[i];
    TP->magic = 0;
    BB->magic = 0;
    MX->dl_cleared = 0;
    MX->dl_target = (uint8_t)target;
    MX->dl_switching = 1;
}

/* Clear the next piece; 1 when done. */
static int dl_clear_step(void)
{
    uint32_t total = (MX->dl_target != DELAY_STOCK ? 2u : DL_BUFFERS) * DL_BUFFER_WORDS;
    uint32_t left = DL_CLEAR_WORDS;
    while (left && MX->dl_cleared < total) {
        uint32_t b = MX->dl_cleared / DL_BUFFER_WORDS, at = MX->dl_cleared % DL_BUFFER_WORDS;
        uint32_t n = DL_BUFFER_WORDS - at;
        if (n > left)
            n = left;
        volatile uint32_t *w = (volatile uint32_t *)MX->dl_buf[b] + at;
        for (uint32_t i = 0; i < n; i++)
            w[i] = 0;
        MX->dl_cleared += n;
        left -= n;
    }
    return MX->dl_cleared >= total;
}

/* Delay object vtable slot 0x60042fec (update method 0x60032405) */
void mt_mfx_delay_update(uint8_t *obj)
{
    if (MX->ready != MX_READY || MX->magic != MX_MAGIC) {   /* still booting: the stock delay */
        ((update_fn)FW_DELAY_UPDATE)(obj);
        return;
    }
    uint32_t buf[DL_BUFFERS] = {*(volatile uint32_t *)(obj + DL_BUF0), *(volatile uint32_t *)(obj + DL_BUF1),
                                *(volatile uint32_t *)(obj + DL_BUF2)};
    int have = buf[0] && buf[1] && buf[2];
    uint32_t want = *(volatile uint8_t *)&MX->delay;
    if (want >= DELAYS)
        want = DELAY_STOCK;
    if (!MX->dl_switching && want != MX->dl_active && have)
        dl_switch_to(want, buf);
    if (MX->dl_switching) {
        if (!have)
            return;
        if (MX->dl_buf[0] != buf[0] || MX->dl_buf[1] != buf[1] || MX->dl_buf[2] != buf[2])
            dl_switch_to(MX->dl_target, buf);  /* the buffers moved: start again */
        if (!dl_clear_step())               /* clearing: silent, like the bypass */
            return;
        if (MX->dl_target == DELAY_STOCK)
            for (uint32_t off = DL_STATE_FIRST; off <= DL_STATE_LAST; off += 4)
                *(volatile uint32_t *)(obj + off) = 0;
        else if (MX->dl_target == DELAY_BBD)
            bbd_init(obj, buf[0], buf[1]);
        else
            tape_init(obj, buf[0], buf[1]);
        MX->dl_active = MX->dl_target;
        MX->dl_switching = 0;
        return;
    }
    if (MX->dl_active == DELAY_STOCK) {
        ((update_fn)FW_DELAY_UPDATE)(obj);
        return;
    }
    if (!have)
        return;
    int ready;
    if (MX->dl_active == DELAY_BBD)
        ready = BB->magic == BBD_MAGIC && BB->buf[0] == buf[0] && BB->buf[1] == buf[1];
    else
        ready = TP->magic == TAPE_MAGIC && TP->buf[0] == buf[0] && TP->buf[1] == buf[1];
    if (!ready) {
        dl_switch_to(MX->dl_active, buf);   /* the buffers moved: clear and lay out again */
        return;
    }
    if (MX->dl_active == DELAY_BBD)
        bbd_blocks(obj);
    else
        tape_blocks(obj);
}

/* ---- the Config menu ------------------------------------------------------------ */

#define MENU_ITEM_SIZE      0x20u           /* fields up to +0x18 */
#define MENU_FOLDER_SIZE    0x20u           /* fields up to +0x1e */
#define MENU_OPTIONS        4u              /* stock option item (e.g. Anti-aliasing) */
#define FOLDER_ITEMS        2u              /* Reverb, Delay */

/* option item (type 4): value pointer, option count, option names, and a callback that
 * the menu's apply (cfg_menu_apply, 0x60118658) calls after storing the choice, without
 * checking it for NULL. */
struct menu_options { volatile uint8_t *value; uint32_t count; const char *const *names; void_fn on_apply; };

static void option_applied(void)            /* the hooks and settings_tick see the value */
{
}

static const char *const reverb_names[REVERBS] = {"Stock", "Freeverb", "Plate", "Spring", "Shimmer",
                                                  "\"Gated\"", "Reverse"};
static const struct menu_options reverb_desc = {&MX->reverb, REVERBS, reverb_names, option_applied};
static const char *const delay_names[DELAYS] = {"Stock", "Tape", "BBD"};
static const struct menu_options delay_desc = {&MX->delay, DELAYS, delay_names, option_applied};

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
    MX->ready = 0;                          /* stock reverb and delay until the Pattern screen runs */
    MX->active = REVERB_STOCK;
    MX->switching = 0;
    FV->magic = 0;
    PL->magic = 0;
    SP->magic = 0;
    SH->magic = 0;
    GT->magic = 0;
    MX->dl_active = DELAY_STOCK;
    MX->dl_switching = 0;
    TP->magic = 0;
    BB->magic = 0;
    void *r = ((item_fn)mt_mfx_next_last_item)(node, parent, label, help, type, descriptor);
    mx_init_if_needed();
    malloc_fn alloc = (malloc_fn)FW_MALLOC;
    void *folder = alloc(MENU_FOLDER_SIZE);
    if (folder) {
        ((folder_fn)FW_MENU_FOLDER)(folder, (void *)FW_PROJECT_SETTINGS, "Master FX", FOLDER_ITEMS);
        void *item = alloc(MENU_ITEM_SIZE);
        if (item)
            ((item_fn)FW_MENU_ITEM)(item, folder, "Reverb",
                                    "The master reverb for this project: Stock (the Tracker's own), "
                                    "Freeverb, Plate, Spring, Shimmer, \"Gated\" (a burst as long as Size, "
                                    "10-368 ms) or Reverse (the burst swells instead). The Master Reverb "
                                    "knobs work for all.",
                                    MENU_OPTIONS, &reverb_desc);
        item = alloc(MENU_ITEM_SIZE);
        if (item)
            ((item_fn)FW_MENU_ITEM)(item, folder, "Delay",
                                    "The master delay for this project: Stock (the Tracker's own), Tape or "
                                    "BBD. The Master Delay knobs work for all.",
                                    MENU_OPTIONS, &delay_desc);
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
    int n = ((snprintf_fn)FW_SNPRINTF)(path, size, "%s/%s/masterfx.bin",
                                       (const char *)FW_PROJECT_PATH, (const char *)FW_PROJECT_NAME);
    return n > 0 && (uint32_t)n < size;
}

static int file_manager_idle(void)
{
    const volatile uint8_t *fm = (const volatile uint8_t *)FW_FM;
    uint8_t state = fm[FM_STATE];
    return (state == 0 || state == 0x0C) && (fm[FM_SUBSTATE] & 0xFD) == 0;
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
    ok = ok && get32(buf) == FILE_MAGIC && get32(buf + 8) == data_sum(buf + 4) && buf[4] < REVERBS
         && buf[5] < OLD_DELAYS;
    if (!ok)
        return 0;
    MX->reverb = MX->saved = buf[4];
    /* a file from before mf7 saying Delay 3 meant "Gated", which is a reverb now: that project
     * plays the stock delay (the reverb choice is the user's, never auto-changed) */
    MX->delay = MX->saved_delay = buf[5] < DELAYS ? buf[5] : DELAY_STOCK;
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
    uint8_t reverb = MX->reverb, delay = MX->delay;
    put32(buf, FILE_MAGIC);
    buf[4] = reverb;
    buf[5] = delay;
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
        MX->saved_delay = delay;
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
    int unsaved = project_unsaved();
    if (session != MX->session) {
        int save_as = session == MX->save_as;   /* the same project under its new name */
        MX->save_as = 0;
        MX->session = session;
        MX->saved_valid = 0;                /* after a Save As: written to the new folder */
        MX->wait = SAVE_DELAY;
        MX->load = (uint8_t)(!unsaved && !save_as);
        if (!save_as) {
            MX->reverb = REVERB_STOCK;      /* another project: its file, once read, decides */
            MX->delay = DELAY_STOCK;
        }
    }
    if (!file_manager_idle())
        return;
    if (MX->load) {
        MX->load = 0;
        settings_load();                    /* none or a bad one: stock */
        MX->saved = MX->reverb;
        MX->saved_delay = MX->delay;
        MX->saved_valid = 1;                /* nothing to write until the choice changes */
        return;
    }
    if (MX->reverb >= REVERBS)
        MX->reverb = REVERB_STOCK;
    if (MX->delay >= DELAYS)
        MX->delay = DELAY_STOCK;
    if (unsaved || (MX->saved_valid && MX->reverb == MX->saved && MX->delay == MX->saved_delay)) {
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
 * project's choices, and the unit has booted, so a swapped reverb or delay may run */
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

/* Project screen vtable slot 0x6004193c (update method 0x600ead59; chained): New
 * Project, Open and Save As happen there, so a new project is seen before its first
 * save even when no other hooked screen showed in between. */
uint32_t mt_mfx_project_update(void *screen, void *a1, void *a2, void *a3)
{
    settings_tick();
    return ((project_update_fn)mt_mfx_next_project_update)(screen, a1, a2, a3);
}

/* 0x60114eaa in the save request 0x60114e34: "bl strncpy(fm + 0x340, name, 0x21)",
 * run only for Save As, after the new path is copied. The next tick sees the new
 * session and keeps the choices for it. */
char *mt_mfx_save_name(char *dst, const char *src, uint32_t n)
{
    char *r = ((strncpy_fn)mt_mfx_next_save_name)(dst, src, n);
    mx_init_if_needed();
    MX->save_as = project_session();
    return r;
}

/* ---- where the state is, for the emulator tests (tools/emu/reverb_rig.py): a marker,
 * then the address of each part, which the patcher relocates with the rest ----------- */
const void *const mt_mfx_ram_map[] = {
    (const void *)0x50414D52u,              /* "RMAP" */
    &mfx_state_ram,
    &fv_state_ram,
    &tape_state_ram,
    &plate_state_ram,
    &spring_state_ram,
    &bbd_state_ram,
    &shimmer_state_ram,
    &gated_state_ram,
};

/* ---- version text: Config shows 1.2.0-<MASTERFX_VERSION> ------------------------ */
const char mt_version_fmt[] = "%d.%d.%d-" MASTERFX_VERSION;
