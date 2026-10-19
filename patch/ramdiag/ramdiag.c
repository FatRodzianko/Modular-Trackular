/*
 * ramdiag: checks on hardware whether RAM regions that no firmware code refers to
 * stay untouched in use (FINDINGS section 15), and how far the heap grows. A
 * diagnostic for stock 1.2.0: install it on its own, not with other patches (it uses
 * the same hook sites as humanize).
 *
 * At power-on it only resets its own state (in the Config menu constructor). Nothing
 * else happens until the Pattern screen runs: its first update fills each candidate
 * region with a pattern that depends on the address. From then on it counts, per
 * region, the 4-byte words that still hold the pattern:
 *  - DTCM above the heap limit   0x20051c68-0x20052fff (5,016 bytes)
 *  - SDRAM top                   0x83ff9bf0-0x83ffffff (25,616 bytes)
 *  - ITCM tail                   0x0001fb38-0x0001ffff (1,224 bytes; aligned access only)
 *  - DTCM after the audio pool   0x200094b8-0x200097ff (840 bytes)
 * and the heap's high-water mark: the newlib break (*0x2002fb9c) above the heap
 * start 0x2002fc68, out of 139,264 bytes (the heap never shrinks).
 *
 * Results:
 *  - Config > Project Settings > RAM diag: one line per region ("untouched/size"),
 *    the heap high-water mark and the report count. Updated about once a second while
 *    Config is open; shown when the list is drawn.
 *  - ramdiag.txt in the card's root: written when Config opens and about once a
 *    minute on the Pattern and Config screens, so the last numbers survive a freeze.
 *
 * Its own state (under 256 bytes) is its .mt_ram, placed by the patcher in the OCRAM
 * tail, which it doesn't test.
 */
#include <stdint.h>

#ifndef RAMDIAG_VERSION
#define RAMDIAG_VERSION "RD2"
#endif

/* ---- firmware 1.2.0 addresses (function pointers carry the Thumb bit) ----------- */
#define FW_FM               0x2001E488u     /* file manager; *FW_FM = FS object */
#define FW_PROJECT_SETTINGS 0x20028714u     /* Config menu folder "Project Settings" */
#define FW_CLOCK            0x20027D18u     /* -> object; vtable[0](obj) = milliseconds */
#define FW_HEAP_BREAK       0x2002FB9Cu     /* newlib _sbrk's current end (0 before first use) */
#define FW_HEAP_START       0x2002FC68u
#define FW_HEAP_LIMIT       0x20051C68u

#define FW_MENU_FOLDER      0x60118395u     /* (node, parent, label, capacity) */
#define FW_MENU_ITEM        0x601183E9u     /* (node, parent, label, help, type, descriptor) */
#define FW_MALLOC           0x600A3589u     /* (size) */
#define FW_SNPRINTF         0x6016712Du
#define FW_HEAP_FREE        0x6010C255u     /* (): mallinfo().fordblks ("free heap") */
#define FW_STACK_MAX        0x6010C269u     /* (): deepest stack use ("max stack") */
#define FW_PATTERN_UPDATE   0x600E1F99u     /* Pattern screen update (vtable slot 0x600403dc) */
#define FW_CONFIG_UPDATE    0x600C6475u     /* Config screen update (vtable slot 0x6003b5f8) */

/* FS object methods (offsets in its vtable), as colorsong and humanize use them */
struct fs_open_result { uint32_t handle; uint8_t ok; uint8_t pad[3]; };
typedef void (*fs_open_fn)(struct fs_open_result *res, void *fs, const char *path, uint32_t mode, uint32_t x);
typedef int (*fs_write_fn)(void *fs, const void *buf, uint32_t size, uint32_t handle);
typedef void (*fs_close_fn)(void *fs, uint32_t handle, uint32_t x);
typedef void (*fs_remove_fn)(void *fs, const char *path);
#define FS_REMOVE           0x14u
#define FS_OPEN             0x2Cu
#define FS_WRITE            0x34u
#define FS_CLOSE            0x40u
#define FS_MODE_WRITE       3u              /* FatFs create-always + RW, as the pattern save */
#define WRITE_PIECE         256u

typedef uint32_t (*u32_fn)(void);
typedef uint32_t (*clock_fn)(void *clock);
typedef void *(*folder_fn)(void *node, void *parent, const char *label, uint32_t capacity);
typedef void *(*item_fn)(void *node, void *parent, const char *label, const char *help,
                         uint32_t type, const void *descriptor);
typedef void *(*malloc_fn)(uint32_t size);
typedef int (*snprintf_fn)(char *buf, uint32_t size, const char *fmt, ...);
typedef void (*update_fn)(void *screen, void *a1, void *a2, void *a3);

/* ---- regions ----------------------------------------------------------------------- */

enum { REG_DTCM_HEAP_GAP, REG_SDRAM_TOP, REG_ITCM_TAIL, REG_DTCM_AUDIO_GAP, REGIONS };

static const struct { uint32_t start, end; const char *name; } regions[REGIONS] = {
    {0x20051C68u, 0x20053000u, "DTCM above heap"},
    {0x83FF9BF0u, 0x84000000u, "SDRAM top"},
    {0x0001FB38u, 0x00020000u, "ITCM tail"},
    {0x200094B8u, 0x20009800u, "DTCM after audio"},
};

static uint32_t pattern(uint32_t addr)
{
    return addr ^ 0x5AC3A53Cu;
}

/* ---- state ------------------------------------------------------------------------- */

#define RD_MAGIC            0x31444152u     /* "RAD1" */
#define TEXT                20u

enum { LINE_HEAP = REGIONS, LINE_REPORTS, LINES };

struct rd_state {
    uint32_t magic;
    uint32_t filled;                        /* the regions hold the pattern since this boot */
    uint32_t untouched[REGIONS];            /* words still holding the pattern */
    uint32_t heap_high;                     /* highest break seen, bytes above the heap start */
    uint32_t reports;                       /* ramdiag.txt writes this boot */
    uint32_t last_scan, last_report;        /* milliseconds */
    uint32_t in_config;                     /* the last update was the Config screen's */
    char text[LINES][TEXT];                 /* what the RAM diag menu items show */
};
/* This patch's RAM (.mt_ram, neither loaded nor zeroed; tools/patch_ptf.py places it in
 * a free RAM region, never one this patch tests). Used from the Config menu constructor
 * at startup. */
static struct rd_state rd_state_ram __attribute__((section(".mt_ram")));
#define RD ((volatile struct rd_state *)&rd_state_ram)
_Static_assert(sizeof(struct rd_state) <= 256, "ramdiag state must stay small");

void *memset(void *dst, int c, unsigned n);
void *memset(void *dst, int c, unsigned n)
{
    uint8_t *d = dst;
    while (n--)
        *d++ = (uint8_t)c;
    return dst;
}

static uint32_t now_ms(void)
{
    void *clock = *(void **)FW_CLOCK;
    if (!clock)
        return 0;
    return ((clock_fn)(*(void ***)clock)[0])(clock);
}

static void state_reset(void)               /* at power-on: forget the previous session */
{
    RD->filled = 0;
    for (uint32_t i = 0; i < REGIONS; i++)
        RD->untouched[i] = 0;
    RD->heap_high = 0;
    RD->reports = 0;
    RD->last_scan = RD->last_report = 0;
    RD->in_config = 0;
    for (uint32_t i = 0; i < LINES; i++) {
        RD->text[i][0] = '-';
        RD->text[i][1] = 0;
    }
    RD->magic = RD_MAGIC;
}

static void fill_regions(void)
{
    for (uint32_t r = 0; r < REGIONS; r++)
        for (uint32_t a = regions[r].start; a < regions[r].end; a += 4)
            *(volatile uint32_t *)a = pattern(a);
    RD->filled = 1;
}

static void scan(void)
{
    if (RD->magic != RD_MAGIC || !RD->filled)
        return;
    for (uint32_t r = 0; r < REGIONS; r++) {
        uint32_t n = 0;
        for (uint32_t a = regions[r].start; a < regions[r].end; a += 4)
            n += *(volatile uint32_t *)a == pattern(a);
        RD->untouched[r] = n;
        ((snprintf_fn)FW_SNPRINTF)((char *)RD->text[r], TEXT, "%u/%u", (unsigned)(n * 4),
                                   (unsigned)(regions[r].end - regions[r].start));
    }
    uint32_t brk = *(volatile uint32_t *)FW_HEAP_BREAK;
    if (brk > FW_HEAP_START && brk - FW_HEAP_START > RD->heap_high)
        RD->heap_high = brk - FW_HEAP_START;
    ((snprintf_fn)FW_SNPRINTF)((char *)RD->text[LINE_HEAP], TEXT, "%u B",
                               (unsigned)RD->heap_high);
    ((snprintf_fn)FW_SNPRINTF)((char *)RD->text[LINE_REPORTS], TEXT, "%u",
                               (unsigned)RD->reports);
}

/* ---- ramdiag.txt -------------------------------------------------------------------- */

static void report(void)
{
    void *fs = *(void **)FW_FM;
    if (!fs || RD->magic != RD_MAGIC || !RD->filled)
        return;
    scan();
    char buf[640];
    snprintf_fn pr = (snprintf_fn)FW_SNPRINTF;
    int n = pr(buf, sizeof buf, "ramdiag " RAMDIAG_VERSION "\nreport %u, at %u ms\n",
               (unsigned)(RD->reports + 1), (unsigned)now_ms());
    for (uint32_t r = 0; r < REGIONS && n > 0 && (uint32_t)n < sizeof buf; r++)
        n += pr(buf + n, sizeof buf - n, "%s 0x%08x-0x%08x: %u of %u bytes untouched\n",
                regions[r].name, (unsigned)regions[r].start, (unsigned)(regions[r].end - 1),
                (unsigned)(RD->untouched[r] * 4), (unsigned)(regions[r].end - regions[r].start));
    uint32_t brk = *(volatile uint32_t *)FW_HEAP_BREAK;
    if (n > 0 && (uint32_t)n < sizeof buf)
        n += pr(buf + n, sizeof buf - n,
                "heap: break 0x%08x, high-water %u of %u bytes, free inside %u\n"
                "max stack %u of 20480 bytes\n",
                (unsigned)brk, (unsigned)RD->heap_high, (unsigned)(FW_HEAP_LIMIT - FW_HEAP_START),
                (unsigned)((u32_fn)FW_HEAP_FREE)(), (unsigned)((u32_fn)FW_STACK_MAX)());
    if (n <= 0)
        return;
    uint32_t size = (uint32_t)n < sizeof buf ? (uint32_t)n : sizeof buf - 1;

    void **vt = *(void ***)fs;
    struct fs_open_result res = {0, 0, {0, 0, 0}};
    ((fs_remove_fn)vt[FS_REMOVE / 4])(fs, "ramdiag.txt");
    ((fs_open_fn)vt[FS_OPEN / 4])(&res, fs, "ramdiag.txt", FS_MODE_WRITE, 0);
    if (!res.ok)
        return;
    for (uint32_t off = 0; off < size; off += WRITE_PIECE) {
        uint32_t piece = size - off < WRITE_PIECE ? size - off : WRITE_PIECE;
        if (((fs_write_fn)vt[FS_WRITE / 4])(fs, buf + off, piece, res.handle) != (int)piece)
            break;
    }
    ((fs_close_fn)vt[FS_CLOSE / 4])(fs, res.handle, 1);
    RD->reports++;
    pr((char *)RD->text[LINE_REPORTS], TEXT, "%u", (unsigned)RD->reports);
}

/* ---- hooks -------------------------------------------------------------------------- */

#define SCAN_EVERY          1000u           /* ms, while Config is open */
#define REPORT_EVERY        60000u          /* ms */

/* Pattern screen vtable slot 0x600403dc (update method 0x600e1f99) */
void mt_rd_pattern_update(void *screen, void *a1, void *a2, void *a3)
{
    ((update_fn)FW_PATTERN_UPDATE)(screen, a1, a2, a3);
    if (RD->magic != RD_MAGIC)
        return;
    RD->in_config = 0;
    if (!RD->filled) {
        fill_regions();
        RD->last_report = now_ms();
        return;
    }
    uint32_t t = now_ms();
    if (t - RD->last_report >= REPORT_EVERY) {
        RD->last_report = t;
        report();
    }
}

/* Config screen vtable slot 0x6003b5f8 (update method 0x600c6475) */
void mt_rd_config_update(void *screen, void *a1, void *a2, void *a3)
{
    if (RD->magic == RD_MAGIC && RD->filled) {
        uint32_t t = now_ms();
        if (!RD->in_config || t - RD->last_report >= REPORT_EVERY) {
            RD->in_config = 1;               /* Config just opened, or a minute passed */
            RD->last_report = RD->last_scan = t;
            report();
        } else if (t - RD->last_scan >= SCAN_EVERY) {
            RD->last_scan = t;
            scan();
        }
    }
    ((update_fn)FW_CONFIG_UPDATE)(screen, a1, a2, a3);
}

/* ---- the Config menu ---------------------------------------------------------------- */

#define MENU_FOLDER_SIZE    0x20u
#define MENU_ITEM_SIZE      0x20u
#define MENU_TEXT           5u              /* stock text item (e.g. Current version) */

/* text item: a pointer to the text's pointer; the texts are in RD */
static const char *const texts[LINES] = {
    (const char *)&RD->text[0], (const char *)&RD->text[1], (const char *)&RD->text[2],
    (const char *)&RD->text[3], (const char *)&RD->text[4], (const char *)&RD->text[5],
};

/* replaces "bl 0x60118394" at 0x60118b00: Project Settings, 10 items -> 11 */
void *mt_rd_settings_folder(void *node, void *parent, const char *label, uint32_t capacity)
{
    return ((folder_fn)FW_MENU_FOLDER)(node, parent, label, capacity + 1);
}

/* replaces the constructor's last "bl 0x601183e8" (Licenses) at 0x601193e6 */
void *mt_rd_last_item(void *node, void *parent, const char *label, const char *help,
                      uint32_t type, const void *descriptor)
{
    void *r = ((item_fn)FW_MENU_ITEM)(node, parent, label, help, type, descriptor);
    state_reset();
    malloc_fn alloc = (malloc_fn)FW_MALLOC;
    void *folder = alloc(MENU_FOLDER_SIZE);
    if (!folder)
        return r;
    ((folder_fn)FW_MENU_FOLDER)(folder, (void *)FW_PROJECT_SETTINGS, "RAM diag", LINES);
    static const char *const labels[LINES] = {
        "DTCM above heap", "SDRAM top", "ITCM tail", "DTCM after audio", "Heap high-water", "Reports written",
    };
    static const char item_help[] = "ramdiag: bytes still untouched since the Pattern screen first ran, "
                               "and the heap's high-water mark. Also in ramdiag.txt.";
    for (uint32_t i = 0; i < LINES; i++) {
        void *item = alloc(MENU_ITEM_SIZE);
        if (item)
            ((item_fn)FW_MENU_ITEM)(item, folder, labels[i], item_help, MENU_TEXT, &texts[i]);
    }
    return r;
}

/* ---- version text: Config shows 1.2.0-<RAMDIAG_VERSION> ----------------------------- */
const char mt_version_fmt[] = "%d.%d.%d-" RAMDIAG_VERSION;
