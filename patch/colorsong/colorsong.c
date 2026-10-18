/*
 * colorsong: color the Song screen's track blocks by track content.
 * Target: Polyend Tracker+ firmware 1.2.0 (stock image, see docs/FINDINGS.md).
 *
 * Each filled block (one track in one pattern) gets a color family from the set of
 * instruments its steps use, and a variant from everything else (step positions,
 * notes, instrument per step, FX types and values, pattern length). When the Song
 * screen first shows a project, colors are handed out in order of first appearance,
 * top to bottom, left to right: the first family is red and later families follow
 * the golden angle around the color wheel; within a family the first variant gets
 * the main shade and later, different variants get clearly different shades.
 * Colors are sticky: a family or variant keeps its color while it is in the song, and
 * something new takes the next color in a rotation that its peers aren't using, so a
 * color given back by an edit isn't handed straight back to the edited block. Identical content always gets
 * an identical color, whichever column it sits in. The assignments are saved next to
 * the project (<project folder>/colorsong.bin) and loaded the next time its Song
 * screen opens, so colors survive power cycles.
 * "Humanize" FX are ignored (Micro-move m, Volume/Velocity V): adding or changing them
 * keeps the color.
 *
 * Fingerprints are kept up to date two ways:
 *  - whenever the firmware recomputes a pattern's track-used mask
 *    (pattern_update_track_usage: pattern load/save/edit, Song screen copies), and
 *  - while the Song screen is open: its update method reads, one file per update,
 *    every playlist pattern without a current fingerprint from
 *    Workspace/patterns/pattern_NN.mtp, and redraws the grid. (Like the stock
 *    firmware, it does SD I/O only from the update method, never from the screen's
 *    start method.) The table is reset when the project
 *    (name + path) differs from the one it was built for, so power-on and project
 *    switches start clean.
 *  - when a project is opened, every pattern file the firmware copies into
 *    Workspace/patterns/ loses its fingerprint, so reopening the same project
 *    (discarding unsaved changes) can't leave colors from the discarded edits.
 * A block whose fingerprint is missing, or was made for a different mask, keeps the
 * stock color.
 */
#include <stdint.h>

/* ---- firmware addresses (1.2.0) ------------------------------------------- */
#define FW_PATTERN_UPDATE_TRACK_USAGE 0x600C3999u   /* Thumb */
#define FW_EVE_COLOR_RGB              0x6011F6B1u
#define FW_SCR_SONG_SET_GRID_DATA     0x600FE25Du
#define FW_SCR_SONG_UPDATE            0x600FDD39u   /* Song screen vtable slot 0x60042dd8 */
#define FW_DISP_CTRL_REFRESH          0x60101E85u   /* (display, control index) */
#define FW_DISPLAY                    ((void *)0x20027D64u)
#define SONG_SCREEN_GRID_CTRL         0xAD          /* screen+0xad: song grid control index */
#define FW_FILE_COPY_STEP             0x600C4579u   /* (copier, src, dst): chunked copy */
#define FW_SNPRINTF                   0x6016712Du
#define FW_PATTERN_PATH_FMT           0x6003B294u   /* "Workspace/patterns/pattern_%02d.mtp" */

#define FW_FILEMAN        ((uint8_t *)0x2001E488u)  /* *FW_FILEMAN = file system object */
#define FW_PROJECT_NAME   (FW_FILEMAN + 0x340)      /* strings saved into project.mt */
#define FW_PROJECT_PATH   (FW_FILEMAN + 0x241)
#define FW_CUR_PATTERN    (*(const uint8_t *)0x202046C8u)
#define FW_CUR_PATTERN_BUF (*(const uint8_t *const *)(0x20017274u + 0x14))
#define FW_MASK_LO        ((const uint8_t *)0x202048CCu)  /* lo[p], p = 1..255 */
#define FW_MASK_HI        ((const uint8_t *)0x20204BF6u)
#define FW_PLAYLIST       ((const uint8_t *)0x202045C2u)  /* 255 pattern numbers, 0 = end */

typedef int (*usage_fn)(void *ctx, uint32_t pattern, const uint8_t *data);
typedef void (*color_fn)(uint32_t rgb);
typedef void (*screen_fn)(void *screen);
typedef void (*refresh_fn)(void *display, int ctrl);
typedef int (*snprintf_fn)(char *buf, uint32_t size, const char *fmt, ...);
typedef uint32_t (*copy_fn)(void *copier, const char *src, const char *dst, uint32_t a3);

/* File system object methods (offsets in its vtable), as used by 0x600c2350. */
struct fs_open_result { uint32_t handle; uint8_t ok; uint8_t pad[3]; };
typedef void (*fs_open_fn)(struct fs_open_result *res, void *fs, const char *path, uint32_t mode, uint32_t x);
typedef int (*fs_read_fn)(void *fs, void *buf, uint32_t size, uint32_t handle);
typedef void (*fs_close_fn)(void *fs, uint32_t handle, uint32_t x);
typedef int (*fs_write_fn)(void *fs, const void *buf, uint32_t size, uint32_t handle);
typedef void (*fs_remove_fn)(void *fs, const char *path);
#define FS_REMOVE 0x14
#define FS_OPEN  0x2C
#define FS_READ  0x30
#define FS_WRITE 0x34
#define FS_CLOSE 0x40
#define FS_MODE_READ  0
#define FS_MODE_WRITE 3                 /* as pattern_write_file: FatFs create-always + RW */

/* Pattern data layout (same as the .mtp file body). */
#define PAT_TRACKS      16
#define PAT_STEPS       128
#define PAT_TRACK0      0x1C        /* first track: 1 length byte + 128 x 6-byte steps */
#define PAT_TRACK_SIZE  0x301
#define NOTE_EMPTY      0xFF
#define INSTR_SAMPLE_MAX 0x2F       /* firmware: instrument > 47 alone counts as content */

/* FX types left out of fingerprints (type numbers as stored in .mtp steps). */
#define FX_MICRO_MOVE   2           /* m */
#define FX_VOLUME       18          /* V - Volume/Velocity */
static const uint8_t ignored_fx[] = {FX_MICRO_MOVE, FX_VOLUME};

static uint32_t counted_fx(uint32_t type)
{
    for (uint32_t i = 0; i < sizeof ignored_fx; i++)
        if (type == ignored_fx[i])
            return 0;
    return type;
}

/* ---- fingerprint table in the free tail of OCRAM ------------------------------
 * The unit's only on-chip OCRAM is the one FlexRAM bank configured as OCRAM: 32 KB at
 * 0x20200000 (the firmware's OCRAM .bss ends at 0x20204f4c, nothing else references
 * the rest). Nothing exists at 0x20280000 on this unit: an access there bus-faults
 * and freezes the device. Normal cacheable memory (MPU region 7).
 */
#define FP_BASE         0x20205000u
#define FP_LIMIT        0x20207FC0u     /* the last 64 bytes of the 32 KB OCRAM bank are humanize's */
#define FP_MAGIC 0x48534F4Cu        /* "LOSH": a table in the current layout */
#define FP_SCAN_DONE 0xFFu

/* Per block key: family index (7 bits) << 9 | variant key (9-bit content hash). */
#define FAM_BITS        7
#define FAM_MAX         127             /* registry size; index 127 = no instrument */
#define FAM_NONE        127
#define VAR_BITS        9
#define VAR_MASK        ((1u << VAR_BITS) - 1)
#define VARIANTS_MAX    512             /* distinct (family, variant) pairs ranked per song */
#define SLOT_NONE       0xFF
#define SHADES          6
#define HUE_SLOTS       20              /* golden-angle hues 0..19 (red, green, purple, ...); >= 12.5 deg apart */
#define VAR_PRESENT     (1u << 24)      /* variant entry: key | shade << 16 | present */

struct fp_pattern {
    uint16_t mask;                  /* track-used mask the fingerprints belong to */
    uint8_t valid;
    uint8_t reserved;
    uint16_t key[PAT_TRACKS];       /* family << 9 | variant */
};

struct fp_table {
    uint32_t magic;
    uint32_t session;               /* hash of project name + path the table belongs to */
    uint32_t updates;               /* fingerprints computed (debug counter) */
    uint32_t file_loads;            /* pattern files read by rescans (debug counter) */
    uint32_t scan_pos;              /* next playlist slot to check, FP_SCAN_DONE when idle */
    uint32_t layout_stamp;          /* inputs the ranks below were computed from */
    uint16_t nfam;                  /* families in the registry */
    uint16_t nvar;                  /* ranked variants */
    uint32_t reserved;
    uint32_t family_key[FAM_MAX];   /* registry: full 32-bit instrument-set hash */
    uint32_t pad0;
    struct fp_pattern pat[256];     /* index = pattern number (1..255) */
    uint8_t family_slot[128];       /* hue slot (golden-angle index), sticky while in the song */
    uint8_t family_present[128];    /* scratch: family seen in the song (layout pass) */
    uint32_t variant[VARIANTS_MAX]; /* key | shade << 16 | VAR_PRESENT; sticky while in the song */
    uint32_t persist_flags;         /* PERSIST_LOADED / PERSIST_DIRTY for this session */
    uint32_t save_wait;             /* Song screen updates to wait before saving */
    uint32_t saved_sum;             /* checksum of the records last loaded or saved */
    uint8_t hue_cursor;             /* next hue slot to try for a new family */
    uint8_t pad1[3];
    uint8_t next_shade[128];        /* per family: next shade to try for a new variant */
};

#define PERSIST_LOADED  1u
#define PERSIST_DIRTY   2u
#define SAVE_DELAY      25u             /* Song screen updates between a change and the save */

#ifndef COLORSONG_NO_FILE_IO
static void colors_load(void);          /* saved colors (defined with the layout code) */
static void colors_save(void);
#endif

#define FP ((struct fp_table *)FP_BASE)
_Static_assert(FP_BASE + sizeof(struct fp_table) <= FP_LIMIT, "fingerprint table must fit in OCRAM");

/* ---- small helpers ----------------------------------------------------------- */
/* GCC may emit memset calls for zeroed locals even in freestanding mode. */
void *memset(void *dst, int c, unsigned n)
{
    volatile uint8_t *p = dst;
    while (n--)
        *p++ = (uint8_t)c;
    return dst;
}

static uint32_t fnv1a(uint32_t h, uint32_t byte)
{
    return (h ^ (byte & 0xFFu)) * 16777619u;
}

static uint32_t fold9(uint32_t h)
{
    return (h ^ (h >> 9) ^ (h >> 18) ^ (h >> 27)) & VAR_MASK;
}

static uint16_t current_mask(uint32_t pattern)
{
    return (uint16_t)(FW_MASK_LO[pattern] | (FW_MASK_HI[pattern] << 8));
}

static void fp_init_if_needed(void)
{
    if (FP->magic == FP_MAGIC)
        return;
    volatile uint32_t *w = (volatile uint32_t *)FP;
    for (uint32_t i = 0; i < sizeof(struct fp_table) / 4; i++)
        w[i] = 0;
    FP->scan_pos = FP_SCAN_DONE;
    for (uint32_t f = 0; f < 128; f++)
        FP->family_slot[f] = SLOT_NONE;
    FP->magic = FP_MAGIC;
}

static void fp_invalidate_all(void)
{
    for (uint32_t p = 0; p < 256; p++)
        FP->pat[p].valid = 0;
}

/* Family registry: maps a 32-bit instrument-set hash to a 7-bit index, so different
 * sets never share a family by accident. Reset (with all fingerprints) when a new
 * pattern might not fit; indices are only meaningful together with the fingerprints. */
static void family_reset(void)
{
    FP->nfam = 0;
    FP->nvar = 0;
    FP->layout_stamp = 0;
    FP->persist_flags = 0;              /* (re)load the project's saved colors */
    FP->saved_sum = 0;
    FP->hue_cursor = 0;
    for (uint32_t f = 0; f < 128; f++)
        FP->next_shade[f] = 0;
    for (uint32_t f = 0; f < 128; f++)
        FP->family_slot[f] = SLOT_NONE;
    fp_invalidate_all();
}

static void family_make_room(void)
{
    if (FP->nfam > FAM_MAX - PAT_TRACKS)
        family_reset();
}

static uint32_t family_index(uint32_t key32)
{
    for (uint32_t i = 0; i < FP->nfam; i++)
        if (FP->family_key[i] == key32)
            return i;
    if (FP->nfam >= FAM_MAX)            /* can't happen after family_make_room() */
        return FAM_NONE;
    FP->family_key[FP->nfam] = key32;
    return FP->nfam++;
}

/* Registry lookup without adding (for saved colors of sets not in this song). */
static uint32_t family_lookup(uint32_t key32)
{
    for (uint32_t i = 0; i < FP->nfam; i++)
        if (FP->family_key[i] == key32)
            return i;
    return FAM_MAX;
}

static uint32_t hash_str(uint32_t h, const uint8_t *s, uint32_t max)
{
    for (uint32_t i = 0; i < max && s[i]; i++)
        h = fnv1a(h, s[i]);
    return fnv1a(h, 0);
}

static uint32_t project_session(void)
{
    uint32_t h = hash_str(2166136261u, FW_PROJECT_NAME, 64);
    return hash_str(h, FW_PROJECT_PATH, 255) | 1;   /* never 0 */
}

/* Fingerprint one track (1 length byte + 128 steps). `last` = pattern length - 1. */
static void fp_track(struct fp_pattern *fp, uint32_t t, const uint8_t *track, uint32_t last)
{
    uint32_t instr[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    uint32_t any_instr = 0;
    uint32_t shade = fnv1a(2166136261u, last);

    for (uint32_t s = 0; s <= last; s++) {
        const uint8_t *st = track + 1 + s * 6;
        uint32_t note = st[0], ins = st[1];
        uint32_t f1t = counted_fx(st[2]), f1v = f1t ? st[3] : 0;   /* ignored FX = empty slot */
        uint32_t f2t = counted_fx(st[4]), f2v = f2t ? st[5] : 0;
        uint32_t has_note = note != NOTE_EMPTY;
        if (!has_note && !f1t && !f2t && ins <= INSTR_SAMPLE_MAX)
            continue;                       /* empty step */
        if (has_note || ins > INSTR_SAMPLE_MAX) {
            instr[ins >> 5] |= 1u << (ins & 31);
            any_instr = 1;
        }
        shade = fnv1a(shade, s);
        shade = fnv1a(shade, note);
        shade = fnv1a(shade, has_note ? ins : 0);
        shade = fnv1a(shade, f1t);
        shade = fnv1a(shade, f1v);
        shade = fnv1a(shade, f2t);
        shade = fnv1a(shade, f2v);
    }

    uint32_t hue = 2166136261u;
    for (uint32_t i = 0; i < 8; i++)
        for (uint32_t b = 0; b < 32; b += 8)
            hue = fnv1a(hue, instr[i] >> b);
    uint32_t fam = any_instr ? family_index(hue) : FAM_NONE;
    fp->key[t] = (uint16_t)(fam << VAR_BITS | fold9(shade));
}

static uint32_t clamp_last(uint32_t last)
{
    return last >= PAT_STEPS ? PAT_STEPS - 1 : last;
}

/* Fingerprint a whole pattern held in RAM (the firmware's own buffers). */
static void fp_from_ram(uint32_t pattern, const uint8_t *data)
{
    family_make_room();
    struct fp_pattern *fp = &FP->pat[pattern];
    uint32_t last = clamp_last(data[PAT_TRACK0]);
    for (uint32_t t = 0; t < PAT_TRACKS; t++)
        fp_track(fp, t, data + PAT_TRACK0 + t * PAT_TRACK_SIZE, last);
    fp->mask = current_mask(pattern);
    fp->valid = 1;
    FP->updates++;
}

/* Read exactly `size` bytes in pieces of at most 256. FatFs only reads straight into
 * the caller's buffer for requests of a full sector (512) or more; smaller pieces are
 * always copied from the file's own sector buffer, like the firmware's small reads. */
#define READ_PIECE 256
#ifndef COLORSONG_NO_FILE_IO
static int fs_read_all(void *fs, void **vt, uint8_t *buf, uint32_t size, uint32_t handle)
{
    while (size) {
        uint32_t n = size < READ_PIECE ? size : READ_PIECE;
        if (((fs_read_fn)vt[FS_READ / 4])(fs, buf, n, handle) != (int)n)
            return 0;
        buf += n;
        size -= n;
    }
    return 1;
}

/* Fingerprint Workspace/patterns/pattern_NN.mtp, one track at a time (small stack
 * buffer, ~0.8 KB). */
static int fp_from_file(uint32_t pattern)
{
    void *fs = *(void **)FW_FILEMAN;
    if (!fs)
        return 0;
    void **vt = *(void ***)fs;
    char path[64];
    uint8_t buf[PAT_TRACK_SIZE + 3];
    struct fs_open_result res = {0, 0, {0, 0, 0}};

    ((snprintf_fn)FW_SNPRINTF)(path, sizeof path, (const char *)FW_PATTERN_PATH_FMT, (int)pattern);
    ((fs_open_fn)vt[FS_OPEN / 4])(&res, fs, path, 0, 0);
    if (!res.ok)
        return 0;

    family_make_room();
    struct fp_pattern *fp = &FP->pat[pattern];
    fp->valid = 0;
    int ok = fs_read_all(fs, vt, buf, PAT_TRACK0, res.handle);
    uint32_t last = 0;
    for (uint32_t t = 0; ok && t < PAT_TRACKS; t++) {
        ok = fs_read_all(fs, vt, buf, PAT_TRACK_SIZE, res.handle);
        if (!ok)
            break;
        if (t == 0)
            last = clamp_last(buf[0]);
        fp_track(fp, t, buf, last);
    }
    ((fs_close_fn)vt[FS_CLOSE / 4])(fs, res.handle, 1);
    if (ok) {
        fp->mask = current_mask(pattern);
        fp->valid = 1;
        FP->updates++;
        FP->file_loads++;
    }
    return ok;
}
#endif /* COLORSONG_NO_FILE_IO */

/* HSL (h 0..359, s and l 0..100) to 0x00RRGGBB, integer only. */
static uint32_t hsl_rgb(uint32_t h, uint32_t s, uint32_t l)
{
    uint32_t c = (100 - (2 * l > 100 ? 2 * l - 100 : 100 - 2 * l)) * s;   /* chroma x 10000 */
    uint32_t hp = h % 120;                                                /* h mod 120 */
    uint32_t d = hp >= 60 ? hp - 60 : 60 - hp;                            /* |h mod 120 - 60| */
    uint32_t x = c * (60 - d) / 60;
    uint32_t m = l * 100 - c / 2;
    uint32_t r, g, b;
    switch (h / 60) {
    case 0: r = c; g = x; b = 0; break;
    case 1: r = x; g = c; b = 0; break;
    case 2: r = 0; g = c; b = x; break;
    case 3: r = 0; g = x; b = c; break;
    case 4: r = x; g = 0; b = c; break;
    default: r = c; g = 0; b = x; break;
    }
    r = ((r + m) * 255 + 5000) / 10000;
    g = ((g + m) * 255 + 5000) / 10000;
    b = ((b + m) * 255 + 5000) / 10000;
    return (r << 16) | (g << 8) | b;
}

/* ---- hook 1: wraps every call to pattern_update_track_usage ----------------- */
/* COLORSONG_PASSTHROUGH: diagnostic build where every hook only calls the original
 * (no table, no file I/O, stock colors), to test the hooking itself on hardware. */
#ifdef COLORSONG_PASSTHROUGH
#define PASSTHROUGH 1
#else
#define PASSTHROUGH 0
#endif

int mt_usage_hook(void *ctx, uint32_t pattern, const uint8_t *data)
{
    if (PASSTHROUGH)
        return ((usage_fn)FW_PATTERN_UPDATE_TRACK_USAGE)(ctx, pattern, data);
    int changed = ((usage_fn)FW_PATTERN_UPDATE_TRACK_USAGE)(ctx, pattern, data);
    pattern &= 0xFF;
    if (pattern != 0) {
        fp_init_if_needed();
        fp_from_ram(pattern, data);
    }
    return changed;
}

/* ---- hook 2: Song screen entry (replaces bl scr_song_set_grid_data at 0x600fe402)
 * No file I/O here. Reset the table for a new project, fingerprint the current pattern
 * from RAM (it may hold unsaved edits), and start a rescan that the update hook runs.
 */
void mt_song_enter(void *screen)
{
    if (PASSTHROUGH) {
        ((screen_fn)FW_SCR_SONG_SET_GRID_DATA)(screen);
        return;
    }
    fp_init_if_needed();
    uint32_t session = project_session();
    if (FP->session != session) {
        family_reset();
        FP->session = session;
        uint32_t cur = FW_CUR_PATTERN;
        const uint8_t *buf = FW_CUR_PATTERN_BUF;
        if (cur && buf)
            fp_from_ram(cur, buf);
    }
    FP->scan_pos = 0;
    ((screen_fn)FW_SCR_SONG_SET_GRID_DATA)(screen);
}

/* ---- hook 2b: Song screen update (vtable slot 0x60042dd8) -------------------------
 * Runs where the stock firmware does its own Song-screen SD I/O. Reads at most one
 * pattern file per call, then redraws the grid, then runs the stock update.
 */
void mt_song_update(uint8_t *screen)
{
    if (!PASSTHROUGH && FP->magic == FP_MAGIC && FP->scan_pos < 255) {
        while (FP->scan_pos < 255) {
            uint32_t p = FW_PLAYLIST[FP->scan_pos++];
            if (p == 0) {
                FP->scan_pos = FP_SCAN_DONE;
                break;
            }
            const struct fp_pattern *fp = &FP->pat[p];
            uint16_t mask = current_mask(p);
            if (mask == 0 || (fp->valid && fp->mask == mask))
                continue;
#ifndef COLORSONG_NO_FILE_IO
            if (fp_from_file(p))
                ((refresh_fn)FW_DISP_CTRL_REFRESH)(FW_DISPLAY, (int)(int8_t)screen[SONG_SCREEN_GRID_CTRL]);
#endif
            break;                              /* one file per update */
        }
        if (FP->scan_pos >= 255)
            FP->scan_pos = FP_SCAN_DONE;
        if (FP->scan_pos == FP_SCAN_DONE) {    /* rescan finished: hand out colors */
#ifndef COLORSONG_NO_FILE_IO
            if (!(FP->persist_flags & PERSIST_LOADED)) {
                colors_load();                 /* saved colors first, then new ones */
                FP->persist_flags |= PERSIST_LOADED;
            }
#endif
            ((refresh_fn)FW_DISP_CTRL_REFRESH)(FW_DISPLAY, (int)(int8_t)screen[SONG_SCREEN_GRID_CTRL]);
        }
    } else if (!PASSTHROUGH && FP->magic == FP_MAGIC && (FP->persist_flags & PERSIST_DIRTY)) {
#ifndef COLORSONG_NO_FILE_IO
        if (FP->save_wait)
            FP->save_wait--;
        else {
            FP->persist_flags &= ~PERSIST_DIRTY;
            colors_save();
        }
#endif
    }
    ((screen_fn)FW_SCR_SONG_UPDATE)(screen);
}

/* ---- hook 3: project open copies project/patterns/pattern_NN.mtp to Workspace ----
 * Replaces the copier call at 0x601145ce. Whatever the copier does with the file
 * (copy a chunk, finish, or skip a missing source), the Workspace pattern it targets
 * is no longer described by our fingerprint, so drop it; the next Song screen visit
 * reads the file again.
 */
static uint32_t workspace_pattern_number(const char *path)
{
    static const char prefix[] = "Workspace/patterns/pattern_";
    uint32_t i = 0, n = 0;
    for (; prefix[i]; i++)
        if (path[i] != prefix[i])
            return 0;
    for (; path[i] >= '0' && path[i] <= '9'; i++) {
        n = n * 10 + (uint32_t)(path[i] - '0');
        if (n > 255)
            return 0;
    }
    return path[i] == '.' ? n : 0;
}

uint32_t mt_copy_hook(void *copier, const char *src, const char *dst, uint32_t a3)
{
    uint32_t r = ((copy_fn)FW_FILE_COPY_STEP)(copier, src, dst, a3);
    if (PASSTHROUGH)
        return r;
    uint32_t p = workspace_pattern_number(dst);
    if (p && FP->magic == FP_MAGIC)
        FP->pat[p].valid = 0;
    return r;
}

/* ---- sticky color slots ---------------------------------------------------------
 * Families get hue slots (golden-angle index) and variants get one of SHADES shades.
 * A slot is kept while its family/variant is in the song and released when it
 * leaves. Something new takes the lowest hue slot no family in the song uses, or the
 * shade least used by its family's variants in the song. The song is walked top to
 * bottom, tracks left to right, so the first assignment follows order of appearance.
 * Recomputed only when a fingerprint, the registry or the song changes, and new
 * slots are only handed out once the Song screen's file rescan is done.
 */
static uint32_t layout_inputs(const uint8_t *playlist)
{
    uint32_t h = fnv1a(2166136261u, FP->updates);
    h = fnv1a(h, FP->updates >> 8);
    h = fnv1a(h, FP->updates >> 16);
    h = fnv1a(h, FP->nfam);
    for (uint32_t i = 0; i < 255; i++) {
        h = fnv1a(h, playlist[i]);
        if (playlist[i] == 0)
            break;
        h = fnv1a(h, current_mask(playlist[i]));
        h = fnv1a(h, current_mask(playlist[i]) >> 8);
    }
    return h | 1;
}

static uint32_t variant_find(uint32_t key)
{
    for (uint32_t i = 0; i < FP->nvar; i++)
        if ((FP->variant[i] & 0xFFFF) == key)
            return i;
    return VARIANTS_MAX;
}

/* Runs `body` with `key` set for every filled block in the song that has a current
 * fingerprint, top to bottom, tracks left to right. */
#define FOR_EACH_BLOCK(playlist, key, body)                                        \
    for (uint32_t i_ = 0; i_ < 255 && (playlist)[i_]; i_++) {                     \
        uint32_t p_ = (playlist)[i_];                                              \
        const struct fp_pattern *fp_ = &FP->pat[p_];                               \
        uint16_t m_ = current_mask(p_);                                            \
        if (!fp_->valid || fp_->mask != m_)                                        \
            continue;                                                              \
        for (uint32_t t_ = 0; t_ < PAT_TRACKS; t_++) {                             \
            if (!(m_ & (1u << t_)))                                                \
                continue;                                                          \
            uint32_t key = fp_->key[t_];                                           \
            body                                                                   \
        }                                                                          \
    }

/* New colors come from a rotation: start at the cursor, take the first color no
 * peer in the song is using, and move the cursor past it. A color released by an
 * edit is only handed out again after the rotation has come round. */
static uint32_t hue_slot_used(uint32_t slot)
{
    for (uint32_t f = 0; f < 128; f++)
        if (FP->family_present[f] && FP->family_slot[f] == slot)
            return 1;
    return 0;
}

static uint32_t next_family_slot(void)
{
    uint32_t start = FP->hue_cursor % HUE_SLOTS, slot = start;
    for (uint32_t k = 0; k < HUE_SLOTS; k++) {
        uint32_t s_ = (start + k) % HUE_SLOTS;
        if (!hue_slot_used(s_)) {
            slot = s_;
            break;
        }
    }
    FP->hue_cursor = (uint8_t)((slot + 1) % HUE_SLOTS);
    return slot;
}

static uint32_t shade_used(uint32_t fam, uint32_t shade)
{
    for (uint32_t i = 0; i < FP->nvar; i++) {
        uint32_t v = FP->variant[i];
        if ((v & VAR_PRESENT) && ((v & 0xFFFF) >> VAR_BITS) == fam && ((v >> 16) & 0xFF) == shade)
            return 1;
    }
    return 0;
}

static uint32_t next_variant_shade(uint32_t fam)
{
    uint32_t start = FP->next_shade[fam] % SHADES, shade = start;
    for (uint32_t k = 0; k < SHADES; k++) {
        uint32_t s_ = (start + k) % SHADES;
        if (!shade_used(fam, s_)) {
            shade = s_;
            break;
        }
    }
    FP->next_shade[fam] = (uint8_t)((shade + 1) % SHADES);
    return shade;
}

static void layout_if_needed(const uint8_t *playlist)
{
    if (FP->scan_pos != FP_SCAN_DONE)
        return;                         /* hand out new colors once all files are read */
    uint32_t stamp = layout_inputs(playlist);
    if (FP->layout_stamp == stamp)
        return;

    /* 1. What is in the song now? Keep the slots of everything still present. */
    for (uint32_t f = 0; f < 128; f++)
        FP->family_present[f] = 0;
    for (uint32_t i = 0; i < FP->nvar; i++)
        FP->variant[i] &= ~VAR_PRESENT;
    FOR_EACH_BLOCK(playlist, key, {
        FP->family_present[key >> VAR_BITS] = 1;
        uint32_t v = variant_find(key);
        if (v < VARIANTS_MAX)
            FP->variant[v] |= VAR_PRESENT;
    })

    /* 2. Release the slots of families and variants that left the song. */
    uint32_t n = 0;
    for (uint32_t i = 0; i < FP->nvar; i++)
        if (FP->variant[i] & VAR_PRESENT)
            FP->variant[n++] = FP->variant[i];
    FP->nvar = (uint16_t)n;
    for (uint32_t f = 0; f < 128; f++)
        if (!FP->family_present[f])
            FP->family_slot[f] = SLOT_NONE;

    /* 3. Give new families and variants free colors, in song order. */
    FOR_EACH_BLOCK(playlist, key, {
        uint32_t fam = key >> VAR_BITS;
        if (fam != FAM_NONE && FP->family_slot[fam] == SLOT_NONE)
            FP->family_slot[fam] = (uint8_t)next_family_slot();
        if (variant_find(key) == VARIANTS_MAX && FP->nvar < VARIANTS_MAX)
            FP->variant[FP->nvar++] = key | next_variant_shade(fam) << 16 | VAR_PRESENT;
    })
    FP->layout_stamp = stamp;
    if (FP->persist_flags & PERSIST_LOADED) {
        FP->persist_flags |= PERSIST_DIRTY;
        FP->save_wait = SAVE_DELAY;
    }
}

/* ---- saved colors: <project folder>/colorsong.bin ------------------------------
 * Header: magic "CSG1", u16 family records, u16 variant records, u32 checksum (sum
 * of the FNV-1a hashes of the records, so it doesn't depend on record order). Records are 8 bytes: family {u32 set key, u8 hue slot, 3 pad},
 * variant {u32 set key (0 = no instrument), u16 content key, u8 shade, 1 pad}. All
 * keys are content hashes, so they mean the same in every session. Only what is in
 * the song is saved. A missing, short or damaged file is ignored.
 * All I/O runs in the Song screen's update method, in pieces of at most 256 bytes,
 * mirroring pattern_read_file / pattern_write_file (remove, open mode 3, write, close).
 */
#define FILE_MAGIC      0x31475343u     /* "CSG1" */
#define FILE_HEADER     12
#define RECORD          8
#define CHUNK           256             /* 32 records */

#ifndef COLORSONG_NO_FILE_IO
static const char colors_fmt[] = "%s/%s/colorsong.bin";

static int colors_path(char *path, uint32_t size)
{
    if (!FW_PROJECT_NAME[0])
        return 0;
    int n = ((snprintf_fn)FW_SNPRINTF)(path, size, colors_fmt,
                                       (const char *)FW_PROJECT_PATH, (const char *)FW_PROJECT_NAME);
    return n > 0 && (uint32_t)n < size;
}

static void put32(uint8_t *b, uint32_t v)
{
    b[0] = (uint8_t)v; b[1] = (uint8_t)(v >> 8); b[2] = (uint8_t)(v >> 16); b[3] = (uint8_t)(v >> 24);
}

static uint32_t get32(const uint8_t *b)
{
    return b[0] | b[1] << 8 | b[2] << 16 | (uint32_t)b[3] << 24;
}

struct writer {
    void *fs;
    void **vt;
    uint32_t handle;
    int write;                          /* 0: only count and checksum */
    int ok;
    uint32_t n;
    uint32_t sum;
    uint32_t nfam, nvar;
    uint8_t buf[CHUNK];
};

static void w_flush(struct writer *w)
{
    if (w->write && w->ok && w->n)
        w->ok = ((fs_write_fn)w->vt[FS_WRITE / 4])(w->fs, w->buf, w->n, w->handle) == (int)w->n;
    w->n = 0;
}

static uint32_t record_hash(const uint8_t *r)
{
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < RECORD; i++)
        h = fnv1a(h, r[i]);
    return h;
}

static void w_record(struct writer *w, const uint8_t *r)
{
    w->sum += record_hash(r);
    for (uint32_t i = 0; i < RECORD; i++) {
        if (w->write) {
            w->buf[w->n++] = r[i];
            if (w->n == CHUNK)
                w_flush(w);
        }
    }
}

static void w_records(struct writer *w)
{
    uint8_t r[RECORD];
    w->sum = 0;
    w->nfam = w->nvar = 0;
    for (uint32_t f = 0; f < FAM_NONE; f++) {
        if (!FP->family_present[f] || FP->family_slot[f] == SLOT_NONE)
            continue;
        put32(r, FP->family_key[f]);
        r[4] = FP->family_slot[f]; r[5] = r[6] = r[7] = 0;
        w_record(w, r);
        w->nfam++;
    }
    for (uint32_t i = 0; i < FP->nvar; i++) {
        uint32_t v = FP->variant[i], key = v & 0xFFFF, fam = key >> VAR_BITS;
        if (!(v & VAR_PRESENT))
            continue;
        put32(r, fam == FAM_NONE ? 0 : FP->family_key[fam]);
        r[4] = (uint8_t)(key & VAR_MASK); r[5] = (uint8_t)((key & VAR_MASK) >> 8);
        r[6] = (uint8_t)(v >> 16); r[7] = 0;
        w_record(w, r);
        w->nvar++;
    }
    w_flush(w);
}

static void colors_save(void)
{
    char path[256];
    struct writer w;
    w.fs = *(void **)FW_FILEMAN;
    if (!w.fs || !colors_path(path, sizeof path))
        return;
    w.vt = *(void ***)w.fs;
    w.write = 0;
    w.ok = 1;
    w.n = 0;
    w_records(&w);                      /* checksum and counts */
    if (w.sum == FP->saved_sum)
        return;                         /* same as on the card */
    uint32_t sum = w.sum;

    struct fs_open_result res = {0, 0, {0, 0, 0}};
    ((fs_remove_fn)w.vt[FS_REMOVE / 4])(w.fs, path);
    ((fs_open_fn)w.vt[FS_OPEN / 4])(&res, w.fs, path, FS_MODE_WRITE, 0);
    if (!res.ok)
        return;
    uint8_t h[FILE_HEADER];
    put32(h, FILE_MAGIC);
    h[4] = (uint8_t)w.nfam; h[5] = (uint8_t)(w.nfam >> 8);
    h[6] = (uint8_t)w.nvar; h[7] = (uint8_t)(w.nvar >> 8);
    put32(h + 8, sum);
    w.handle = res.handle;
    w.ok = ((fs_write_fn)w.vt[FS_WRITE / 4])(w.fs, h, FILE_HEADER, w.handle) == FILE_HEADER;
    w.write = 1;
    w_records(&w);
    ((fs_close_fn)w.vt[FS_CLOSE / 4])(w.fs, w.handle, 1);
    if (w.ok && w.sum == sum)
        FP->saved_sum = sum;
}

static void apply_record(const uint8_t *r, int is_family)
{
    uint32_t key32 = get32(r);
    if (is_family) {
        uint32_t f = family_lookup(key32);
        if (f < FAM_MAX && r[4] < SLOT_NONE)
            FP->family_slot[f] = (uint8_t)(r[4] % HUE_SLOTS);
        return;
    }
    uint32_t fam = key32 == 0 ? FAM_NONE : family_lookup(key32);
    if (fam >= FAM_MAX && fam != FAM_NONE)
        return;
    uint32_t key = fam << VAR_BITS | ((r[4] | r[5] << 8) & VAR_MASK);
    if (variant_find(key) == VARIANTS_MAX && FP->nvar < VARIANTS_MAX)
        FP->variant[FP->nvar++] = key | (uint32_t)(r[6] % SHADES) << 16;
}

/* pass 0: verify the checksum; pass 1: apply. Returns the checksum, or 0. */
static uint32_t colors_read(const char *path, int apply)
{
    void *fs = *(void **)FW_FILEMAN;
    void **vt = *(void ***)fs;
    struct fs_open_result res = {0, 0, {0, 0, 0}};
    uint8_t buf[CHUNK];
    ((fs_open_fn)vt[FS_OPEN / 4])(&res, fs, path, FS_MODE_READ, 0);
    if (!res.ok)
        return 0;
    uint32_t sum = 0;
    int ok = ((fs_read_fn)vt[FS_READ / 4])(fs, buf, FILE_HEADER, res.handle) == FILE_HEADER
             && get32(buf) == FILE_MAGIC;
    uint32_t nfam = buf[4] | buf[5] << 8, nvar = buf[6] | buf[7] << 8, want = get32(buf + 8);
    ok = ok && nfam <= FAM_MAX && nvar <= VARIANTS_MAX;
    uint32_t total = ok ? nfam + nvar : 0, done = 0, h = 0;
    while (ok && done < total) {
        uint32_t n = total - done;
        if (n > CHUNK / RECORD)
            n = CHUNK / RECORD;
        ok = ((fs_read_fn)vt[FS_READ / 4])(fs, buf, n * RECORD, res.handle) == (int)(n * RECORD);
        for (uint32_t i = 0; ok && i < n; i++) {
            h += record_hash(buf + i * RECORD);
            if (apply)
                apply_record(buf + i * RECORD, done + i < nfam);
        }
        done += n;
    }
    ((fs_close_fn)vt[FS_CLOSE / 4])(fs, res.handle, 1);
    if (ok && h == want && want)
        sum = want;
    return sum;
}

static void colors_load(void)
{
    char path[256];
    if (!*(void **)FW_FILEMAN || !colors_path(path, sizeof path))
        return;
    uint32_t sum = colors_read(path, 0);
    if (sum && colors_read(path, 1) == sum)
        FP->saved_sum = sum;
    /* Continue the rotations after the loaded colors, so the first edit after a
     * power cycle doesn't get a color back that the song is using. */
    uint32_t top = 0, any = 0;
    for (uint32_t f = 0; f < FAM_NONE; f++)
        if (FP->family_slot[f] != SLOT_NONE && (!any || FP->family_slot[f] >= top)) {
            top = FP->family_slot[f];
            any = 1;
        }
    FP->hue_cursor = any ? (uint8_t)((top + 1) % HUE_SLOTS) : 0;
    for (uint32_t i = 0; i < FP->nvar; i++) {
        uint32_t fam = (FP->variant[i] & 0xFFFF) >> VAR_BITS, sh = (FP->variant[i] >> 16) & 0xFF;
        if ((uint32_t)(FP->next_shade[fam]) <= sh)
            FP->next_shade[fam] = (uint8_t)((sh + 1) % SHADES == 0 ? SHADES : sh + 1);
    }
    for (uint32_t f = 0; f < 128; f++)
        FP->next_shade[f] %= SHADES;
}
#endif /* COLORSONG_NO_FILE_IO */

/* Well-separated shades, in the order a family's variants first get them. */
static const uint8_t shade_sat[SHADES]   = {78, 92, 70, 42, 88, 62};
static const uint8_t shade_light[SHADES] = {50, 70, 34, 58, 80, 26};

/* ---- hook 4: replaces the filled-block color in song_grid_draw --------------
 * Called from the assembly shims with the stock theme color, the song control,
 * the visible row and the track (bit 8 set for the "flagged track" color).
 */
void mt_block_color(uint32_t theme_rgb, const uint8_t *ctrl, uint32_t row, uint32_t track_flags)
{
    uint32_t rgb = theme_rgb;
    if (PASSTHROUGH) {
        ((color_fn)FW_EVE_COLOR_RGB)(rgb);
        return;
    }
    uint32_t track = track_flags & 0xFF;
    const uint8_t *g = *(const uint8_t *const *)(ctrl + 0x34);
    const uint8_t *playlist = *(const uint8_t *const *)(g + 0x04);
    const uint8_t *lo = *(const uint8_t *const *)(g + 0x20);     /* indexed by pattern - 1 */
    const uint8_t *hi = *(const uint8_t *const *)(g + 0x24);
    uint32_t scroll = *(const uint16_t *)(ctrl + 0x32);
    uint32_t pattern = playlist[(row & 0xFF) + scroll];

    if (FP->magic == FP_MAGIC && pattern != 0 && track < PAT_TRACKS) {
        const struct fp_pattern *fp = &FP->pat[pattern];
        uint16_t mask = (uint16_t)(lo[pattern - 1] | (hi[pattern - 1] << 8));
        if (fp->valid && fp->mask == mask) {
            layout_if_needed(playlist);
            uint32_t key = fp->key[track], fam = key >> VAR_BITS;
            uint32_t v = variant_find(key);
            uint32_t slot = FP->family_slot[fam];
            if (v == VARIANTS_MAX || (fam != FAM_NONE && slot == SLOT_NONE)) {
                ((color_fn)FW_EVE_COLOR_RGB)(rgb);   /* no color yet (rescan running) */
                return;
            }
            uint32_t shade = ((FP->variant[v] >> 16) & 0xFF) % SHADES;
            uint32_t h = fam == FAM_NONE ? 0 : ((slot % HUE_SLOTS) * 1375u / 10u) % 360u;  /* golden angle */
            uint32_t s = shade_sat[shade], l = shade_light[shade];
            if (fam == FAM_NONE)
                s = 6;                           /* FX-only block: near-gray */
            if (track_flags & 0x100) {           /* flagged track: same color, much darker */
                s = s / 2;
                l = l * 45 / 100;
                if (l < 14)
                    l = 14;
            }
            rgb = hsl_rgb(h, s, l);
        }
    }
    ((color_fn)FW_EVE_COLOR_RGB)(rgb);
}

/* ---- version text: Config shows 1.2.0-<COLORSONG_VERSION> ---------------------- */
#ifndef COLORSONG_VERSION
#define COLORSONG_VERSION "c10"
#endif
const char mt_version_fmt[] = "%d.%d.%d-" COLORSONG_VERSION;
