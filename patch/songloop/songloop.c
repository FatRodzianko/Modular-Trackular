/*
 * songloop: loop a selection of Song positions (Polyend Tracker+ firmware 1.2.0).
 *
 * In the Song screen's pattern column, with Rec on (red frame), Shift + Up/Down selects
 * a block of song positions from the cursor. The left softkey then reads "Play
 * Selection":
 *  - stopped: the song plays from the block's first position and repeats the block;
 *  - playing: the song plays on and repeats the block once it reaches its end.
 * While the loop runs, the softkey reads "Continue Song" (play the block out, then go on
 * with the song; the selection goes away). Shift + Up/Down still resizes the block, down
 * to a single position that repeats; a one-position block moves with Up/Down. If the
 * play position ends up outside the block, the song goes to the block's start at the
 * end of the pattern (a way to queue any position). Stop, on any screen, ends the loop.
 * With a block, or during the loop, Undo, Add Slot, Delete Slot (also the Delete key)
 * and << Pattern >> (also Shift + jog) do nothing and are greyed out.
 *
 * Icons: the loop shows the repeat signs |: :| in place of the play icon, in the status
 * bar (two glyphs of font 4 redrawn at load time, as wide as play's) and next to the
 * play position in the Song list (a 20x20 bitmap in RAM_G). Armed but not yet in the
 * block: only the opening sign |:.
 *
 * Performance works as in stock firmware (Loop Performance, Shift + Play in Performance).
 * The selection loop is "Loop Performance for a block": leaving Performance while the
 * song plays schedules "Performance off" for the next pattern end (seq+0x6e8 = 0x7091,
 * called right after song_next_position); while the loop is on, the pattern-end hook
 * cancels it the way stock Loop Performance does (0x600abf9d). Continue Song schedules it
 * again through stock's own Continue Song (0x600e6658).
 *
 * The next pattern is preloaded by the sequencer (ITCM) on the last row of track 1, but
 * the pattern ends on track 16. With a Performance play mode other than Fwd on track 1
 * (Bwd, PingPong, Rnd, ...) that request comes too late or at random, and the old
 * pattern plays again. The main loop then requests it itself on the last two rows of
 * track 16, with the same choice (the loop's included).
 *
 * Hooks (all firmware 1.2.0):
 *  - ITCM 0x23c4, 0x2abe: bl song_peek_next (0x7030), the pattern to preload;
 *  - 0x600ad2d0: bl song_next_position at the pattern end;
 *  - 0x600fdd82: bl of the softkey label function in the Song update;
 *  - the Song screen's event callbacks (literal pool 0x600fd948..0x600fd990): jog, Play,
 *    Up, Down, softkeys 0/2/3/4, Delete;
 *  - 0x600fe7ca, 0x600fe7e2, 0x600fe81c, 0x600fe836: bl of the pattern change (softkeys 5/6);
 *  - drawing: 0x601086fa, 0x601088a4 (the cursor frame), 0x60108520/818/9f2/b0c (the
 *    list icon), 0x600cf9f0 (status bar sprintf), 0x600fe20e (softkey colors);
 *  - fonts: 0x60101ade (bl disp_load_fonts in disp_init), 0x600c5f82, 0x600cbede
 *    (bl disp_set_font_set);
 *  - 0x600acd98: the first bl in seq_stop, so every stop ends the loop;
 *  - 0x600cc5ec: bl popup_update in ui_update, once per main loop (the preload).
 * All sites go through chain words (mt_sl_next_*) filled in by tools/patch_ptf.py.
 */
#include <stdint.h>

#ifndef SONGLOOP_VERSION
#define SONGLOOP_VERSION "sl4"
#endif

/* ---- firmware 1.2.0 ------------------------------------------------------------------ */
#define REG8(a)             (*(volatile uint8_t *)(uintptr_t)(a))
#define REG8S(a)            (*(volatile int8_t *)(uintptr_t)(a))
#define REG16(a)            (*(volatile uint16_t *)(uintptr_t)(a))
#define REG16S(a)           (*(volatile int16_t *)(uintptr_t)(a))
#define REG32(a)            (*(volatile uint32_t *)(uintptr_t)(a))
#define FN(addr, type)      ((type)((uintptr_t)(addr) | 1u))

#define FW_SONG             0x20026D04u     /* the Song screen object */
#define SONG                ((void *)FW_SONG)
#define SONG_POPUP          REG8(FW_SONG + 0x11C)   /* nonzero: the inputs go elsewhere */
#define SONG_FOCUS          REG8(FW_SONG + 0xB1)    /* 0 grid, 1 pattern column, 2 tempo */
#define SONG_LABEL_STATE    REG8(FW_SONG + 0xB2)    /* which softkey labels are shown */
#define SONG_SLOTS          REG16(FW_SONG + 0xB4)   /* song length */
#define SONG_CURSOR         REG8(FW_SONG + 0xB6)    /* the pattern column's selected position */
#define SONG_SK0_ID         REG8S(FW_SONG + 0xA4)   /* control id, left softkey (two lines) */
#define SONG_SK0B_ID        REG8S(FW_SONG + 0xA5)   /* control id, Play Song / Stop softkey */
#define SONG_SK_ID(i)       REG8S(FW_SONG + 0xA6 + (i))   /* Undo, Add Slot, Delete Slot, Pattern */
#define SONG_VISIBLE        REG16(FW_SONG + 0xC)    /* visible rows of the pattern column */
#define SONG_REC            REG8(FW_SONG + 0x120)   /* 1: Rec on (red frame) */
#define SONG_D8             REG8(FW_SONG + 0xD8)
#define CTRL_FIRST(c)       (*(volatile uint16_t *)((uint8_t *)(c) + 0x32))  /* first visible slot */
#define CTRL_ROW(c)         (*(volatile uint16_t *)((uint8_t *)(c) + 0x56))  /* the cursor frame's row */
#define CTRL_Y(c)           (*(volatile uint16_t *)((uint8_t *)(c) + 0x1E))  /* the list starts at y + 0x3e */
#define CTRL_ANIM(c)        (*(volatile int8_t *)((uint8_t *)(c) + 0x59))    /* animation offset (px) */
#define ROW_H               27

#define SONG_POS            REG8(0x202046C1u)       /* the play position */
#define PLAYLIST(i)         REG8(0x202045C2u + (i)) /* pattern per position, 0 = end */

#define FW_SEQ              0x20017274u
#define SEQ_PATTERN         REG32(FW_SEQ + 0x14)    /* the playing pattern; its length (last row) at +0x1c */
#define SEQ_SONG_MODE       REG8(FW_SEQ + 0x1C)
#define SEQ_PERFORM         REG8(FW_SEQ + 0x1D)
#define SEQ_1E              REG8(FW_SEQ + 0x1E)
#define SEQ_QUEUED          REG8(FW_SEQ + 0x28)     /* a pattern is queued */
#define SEQ_PLAYING         REG8(FW_SEQ + 0x2A)
#define SEQ_2D              REG8(FW_SEQ + 0x2D)
#define SEQ_TRACK1_PMODE    REG8S(FW_SEQ + 0xB9)    /* track 1's Performance play mode, 0 = Fwd */
#define SEQ_TRACK_ROW(t)    REG16S(FW_SEQ + 0x68 * ((t) + 1) + 0x20)

#define FW_FM               0x2001E488u     /* file manager */
#define FW_DISPLAY          ((void *)0x20027D64u)
#define FW_BUTTONS          (*(void *volatile *)0x200209E8u)
#define BUTTON_SHIFT        0x1D
#define FW_THEME            REG32(0x20027D6Cu)
#define STATUS_CACHE        REG32(0x200209FCu + 0x40)   /* status bar: minutes last drawn */

#define LABEL_PLAY_SEL      0x20            /* own SONG_LABEL_STATE values (stock 0..5) */
#define LABEL_CONTINUE      0x21
#define LABEL_PLAY_SEL_RUN  0x22

#define SEQ_PATTERN_END_ACTION REG32(FW_SEQ + 0x6E8)   /* called at the pattern end, after song_next_position */
#define ACTION_PERFORM_OFF  0x00007091u     /* ITCM: Performance off */
#define ACTION_NONE         0x600ABF9Du     /* a no-op, as stock Loop Performance sets it */
#define FW_PERFORM          ((void *)0x20022C94u)

#define song_next_position  FN(0x600C3970, int (*)(void *fm))
#define song_slot_move      FN(0x600FE078, void (*)(void *song, int delta))
#define song_play_song      FN(0x600FDA58, void (*)(void))
#define song_show_cursor    FN(0x600FED80, void (*)(void *song))
#define button_is_pressed   FN(0x600CF90C, int (*)(void *buttons, int button))
#define disp_set_text       FN(0x60101D5C, void (*)(void *disp, int id, const char *text))
#define disp_set_text2      FN(0x60101D74, void (*)(void *disp, int id, const char *text))
#define disp_set_colors     FN(0x60101DA4, void (*)(void *disp, int id, const uint32_t *colors))
#define disp_refresh        FN(0x60101E84, void (*)(void *disp, int id))
#define disp_sync_refresh   FN(0x60102014, void (*)(void *disp))
#define perform_continue_song FN(0x600E6658, void (*)(void *perform))   /* stock Continue Song, Performance part */
#define fm_request_pattern_load FN(0x600BC938, void (*)(void *fm, int pattern))
#define audio_irq_disable   FN(0x600303D4, void (*)(void))
#define audio_irq_enable    FN(0x600303C8, void (*)(void))
#define eve_color_rgb       FN(0x6011F6B0, void (*)(uint32_t rgb))
#define eve_line_width      FN(0x6011F8B8, void (*)(int width))
#define eve_bitmap_source   FN(0x6011F780, void (*)(uint32_t addr))
#define eve_bitmap_layout   FN(0x6011F7A8, void (*)(int format, int stride, int height))
#define eve_bitmap_size     FN(0x6011F7E4, void (*)(int filter, int wrapx, int wrapy, int width, int height))
#define eve_begin           FN(0x6011F96C, void (*)(int prim))
#define eve_vertex2f        FN(0x6011F6D8, void (*)(int x, int y))
#define eve_end             FN(0x6011F994, void (*)(void))

typedef void (*callback_fn)(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3);
typedef int (*peek_fn)(uint32_t fm, uint32_t r1, uint32_t r2, uint32_t r3);
typedef int (*next_pos_fn)(void *fm);
typedef void (*labels_fn)(void *song);
typedef void (*fonts_fn)(void *disp, const void *table, int n);
typedef void (*font_set_fn)(void *disp, int set);
typedef void (*list_icon_fn)(void *ctrl, int slot, int row, int x, int y0, int rowh);
typedef int (*sprintf8_fn)(char *buf, const char *fmt, int min, int sec, const char *icon, int pat, int pos,
                           const char *suffix);
typedef void (*colors_fn)(void *disp, int id, const uint32_t *colors);
typedef void (*popup_fn)(void *popup);
typedef void (*one_arg_fn)(void *a);
typedef void (*change_fn)(void *song, int delta);

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_sl_next_peek_a);              /* song_peek_next 0x7031 (ITCM 0x23c4) */
CHAIN_WORD(mt_sl_next_peek_b);              /* song_peek_next 0x7031 (ITCM 0x2abe) */
CHAIN_WORD(mt_sl_next_song_next);           /* song_next_position 0x600c3971 */
CHAIN_WORD(mt_sl_next_labels);              /* softkey labels 0x600fedad */
CHAIN_WORD(mt_sl_next_frame_a);             /* eve_line_width 0x6011f8b9 */
CHAIN_WORD(mt_sl_next_frame_b);             /* eve_line_width 0x6011f8b9 */
CHAIN_WORD(mt_sl_next_load_fonts);          /* disp_load_fonts 0x601018e9 */
CHAIN_WORD(mt_sl_next_font_set_a);          /* disp_set_font_set 0x601020ed */
CHAIN_WORD(mt_sl_next_font_set_b);
CHAIN_WORD(mt_sl_next_list_icon_a);         /* list icon 0x60108311 */
CHAIN_WORD(mt_sl_next_list_icon_b);
CHAIN_WORD(mt_sl_next_list_icon_c);
CHAIN_WORD(mt_sl_next_list_icon_d);
CHAIN_WORD(mt_sl_next_status_sprintf);      /* sprintf 0x60167195 */
CHAIN_WORD(mt_sl_next_softkey_colors);      /* disp_set_colors 0x60101da5 */
CHAIN_WORD(mt_sl_next_ui_tick);             /* popup_update 0x600cd0c9 */
CHAIN_WORD(mt_sl_next_stop);                /* 0x600cfbc9, the first call in seq_stop */
CHAIN_WORD(mt_sl_next_change_a);            /* pattern change 0x600fe739 */
CHAIN_WORD(mt_sl_next_change_b);
CHAIN_WORD(mt_sl_next_change_c);
CHAIN_WORD(mt_sl_next_change_d);
CHAIN_WORD(mt_sl_next_jog);                 /* Song callbacks: jog 0x600c9591 */
CHAIN_WORD(mt_sl_next_play);                /* Play 0x600c863d */
CHAIN_WORD(mt_sl_next_up);                  /* Up 0x600caa39 */
CHAIN_WORD(mt_sl_next_down);                /* Down 0x600ca03d */
CHAIN_WORD(mt_sl_next_softkey0);            /* softkey 0 0x600fe601 */
CHAIN_WORD(mt_sl_next_undo);                /* softkey 2 0x600eddc9 */
CHAIN_WORD(mt_sl_next_add);                 /* softkey 3 0x600fe101 */
CHAIN_WORD(mt_sl_next_delete);              /* softkey 4 0x600fe521 */
CHAIN_WORD(mt_sl_next_delete_key);          /* Delete key 0x600d995d */

/* ---- state (.mt_ram: neither loaded nor zeroed; the patcher places it) ----------- */

#define SL_MAGIC            0x504F4C53u     /* "SLOP" */

struct sl_state {
    uint32_t magic;
    uint8_t sel;                            /* a selection exists */
    uint8_t anchor;                         /* where Shift-selecting started */
    uint8_t first, last;                    /* the block (inclusive) */
    uint8_t loop;                           /* Play Selection on; off after Continue Song */
    uint8_t wrap_pending;                   /* decided at the preload: next position = first */
    uint8_t snap;                           /* Shift-selecting: we draw the frame, even for one row */
    uint8_t end;                            /* the edge Shift + Up/Down moves (the other is the anchor) */
    uint8_t entered;                        /* the loop reached the block */
    uint8_t dimmed;                         /* the softkeys Undo..Pattern are greyed by us */
    uint8_t preloaded;                      /* own preload done for this pattern end */
    uint8_t pad;
};
static struct sl_state sl_state_ram __attribute__((section(".mt_ram")));
#define SL (&sl_state_ram)

static void note_entered(void);

static volatile struct sl_state *state(void)    /* RAM outside .bss holds garbage at power-on */
{
    volatile struct sl_state *s = SL;
    if (s->magic != SL_MAGIC) {
        s->sel = s->anchor = s->first = s->last = 0;
        s->loop = s->wrap_pending = s->snap = s->end = s->entered = s->dimmed = s->preloaded = 0;
        s->magic = SL_MAGIC;
    }
    return s;
}

static int shift_held(void) { return button_is_pressed(FW_BUTTONS, BUTTON_SHIFT); }

/* The song plays in song mode. The perform flag doesn't count: Performance doesn't change
 * how the song plays. */
static int song_playing(void)
{
    return !SEQ_2D && SEQ_PLAYING && !SEQ_1E && SEQ_SONG_MODE;
}

static int stopped(void)
{
    return SEQ_2D || !SEQ_PLAYING;
}

static int rec_on(void) { return SONG_REC != 0; }

static int selection_usable(volatile struct sl_state *s)   /* a block of 2+ positions */
{
    return s->sel && s->first < s->last && s->last < SONG_SLOTS;
}

static int slots_locked(void)               /* Undo, Add, Delete and Pattern do nothing */
{
    volatile struct sl_state *s = state();
    return selection_usable(s) || s->loop;
}

static void statusbar_invalidate(void)      /* the status bar redraws its text only on a change */
{
    STATUS_CACHE = 0xFFFFFFFFu;
}

/* ---- playing ------------------------------------------------------------------------- */

/* The pattern to preload after the current one (sequencer interrupt: keep it short). The
 * decision "back to the block's start" is taken here and followed at the pattern end, so
 * the loaded pattern and the position always match. */
static int peek_next(uint32_t fm, uint32_t r1, uint32_t r2, uint32_t r3, uint32_t next)
{
    volatile struct sl_state *s = state();
    uint8_t pos = SONG_POS;
    if (s->loop && s->sel && PLAYLIST(s->first)) {
        int inside = pos >= s->first && pos <= s->last;
        if (inside)
            s->entered = 1;
        /* the block's end, or (after a change during the loop) outside the block: on at the
         * block's start. Not yet in the block: the song plays on up to it. */
        if ((inside && pos == s->last) || (!inside && s->entered)) {
            s->wrap_pending = 1;
            return PLAYLIST(s->first);
        }
    }
    s->wrap_pending = 0;
    return ((peek_fn)next)(fm, r1, r2, r3);
}

int mt_sl_peek_a(uint32_t fm, uint32_t r1, uint32_t r2, uint32_t r3)
{
    return peek_next(fm, r1, r2, r3, mt_sl_next_peek_a);
}

int mt_sl_peek_b(uint32_t fm, uint32_t r1, uint32_t r2, uint32_t r3)
{
    return peek_next(fm, r1, r2, r3, mt_sl_next_peek_b);
}

int mt_sl_song_next(void *fm)               /* the position after the pattern end */
{
    volatile struct sl_state *s = state();
    /* while the loop is on, Performance stays on: cancel a scheduled "Performance off" */
    if (s->loop && SEQ_PERFORM && SEQ_PATTERN_END_ACTION == ACTION_PERFORM_OFF)
        SEQ_PATTERN_END_ACTION = ACTION_NONE;
    if (s->wrap_pending) {
        s->wrap_pending = 0;
        SONG_POS = s->first;
        return 1;
    }
    int r = ((next_pos_fn)mt_sl_next_song_next)(fm);
    note_entered();
    return r;
}

void mt_sl_stop(void *a)                    /* every stop, on any screen, ends the loop */
{
    volatile struct sl_state *s = state();
    if (s->loop)
        statusbar_invalidate();
    s->loop = 0;
    s->wrap_pending = 0;
    ((one_arg_fn)mt_sl_next_stop)(a);
}

/* Once per main loop: with track 1 not on Fwd in Performance, request the next pattern
 * on the last two rows of track 16 (which ends the pattern), once per pattern end. */
void mt_sl_ui_tick(void *popup)
{
    ((popup_fn)mt_sl_next_ui_tick)(popup);
    volatile struct sl_state *s = state();
    if (!song_playing() || !SEQ_PERFORM || !SEQ_TRACK1_PMODE || SEQ_QUEUED) {
        s->preloaded = 0;
        return;
    }
    int len = REG8(SEQ_PATTERN + 0x1C), row = SEQ_TRACK_ROW(15);
    if (row < len - 1) {
        s->preloaded = 0;
        return;
    }
    if (s->preloaded)
        return;
    s->preloaded = 1;
    audio_irq_disable();                    /* the same request as the sequencer's, not at the same time */
    int next = mt_sl_peek_a(FW_FM, 0, 0, 0);
    if (next)
        fm_request_pattern_load((void *)FW_FM, next);
    audio_irq_enable();
}

/* ---- icons --------------------------------------------------------------------------- */

/* The repeat signs |: :| (or |: when armed), in two places:
 *  - status bar: two glyphs of font 4 (L4), like the play/pause/loop icons (two glyphs
 *    each), in place of '`' and '^' (looping) and '{' and '}' (armed), characters the UI
 *    and the keyboard never show. Their widths become play's, so nothing after them moves.
 *    The fonts are loaded at boot and again by disp_set_font_set (two sets); after every
 *    load the glyphs are drawn to font 4's metrics and uploaded with the widths;
 *  - the Song list: 20x20 L4 bitmaps in RAM_G at ICON_RAMG / ICON_RAMG_ARMED.
 * Uploads go through the EVE HAL as disp_init's own: vt[0x30](hal, src, len, ram_g). */
#define ICON_LEFT           0x60            /* '`' */
#define ICON_RIGHT          0x5E            /* '^' */
#define ICON_STR            "`^"
#define ARMED_LEFT          0x7B            /* '{' */
#define ARMED_RIGHT         0x7D            /* '}' */
#define ARMED_STR           "{}"
#define PLAY_LEFT           0x1A            /* the play icon's two glyphs */
#define PLAY_RIGHT          0x1B
#define FONT4_ENTRY         (0x83FCD374u + 3 * 0xACu)   /* disp_load_fonts' table (0xac per font): metrics copy */
#define FONT4_METRIC        REG32(FONT4_ENTRY + 0x9C)  /* RAM_G address of the metric block */
#define FONT4_BITMAP        REG32(FONT4_ENTRY + 0xA0)  /* RAM_G address of the first glyph */
#define GLYPH_BUF           ((uint8_t *)0x83FCD6D0u)    /* disp_load_fonts' unpack buffer, free afterwards */
#define ICON_RAMG           0x1F000u        /* after the icons (to 0x1e8e8), before the snapshot (0x249f0) */
#define ICON_RAMG_ARMED     0x1F100u

/* |: :| 16 px wide from x0: thick bar 2, gap 1, thin bar 1, gap 1, dots 2, middle 2, mirrored */
static const uint8_t REPEAT_COLS[16] = { 2, 2, 0, 1, 0, 3, 3, 0, 0, 3, 3, 0, 1, 0, 2, 2 };

struct canvas { uint8_t *buf; int stride; int w; };

static void pix(struct canvas *c, int x, int y)
{
    uint8_t *g = c->buf;
    if (x >= c->w) {                        /* the second glyph sits elsewhere in the buffer */
        x -= c->w;
        g += c->stride * 64;
    }
    g[y * c->stride + x / 2] |= (x & 1) ? 0x0F : 0xF0;
}

static void draw_repeat(struct canvas *c, int x0, int y0, int y1, int dy1, int dy2, int ncols)
{
    for (int i = 0; i < ncols; i++) {
        int kind = REPEAT_COLS[i];
        if (kind == 1 || kind == 2)
            for (int y = y0; y <= y1; y++)
                pix(c, x0 + i, y);
        else if (kind == 3)
            for (int y = 0; y < 2; y++) {
                pix(c, x0 + i, dy1 + y);
                pix(c, x0 + i, dy2 + y);
            }
    }
}

static void make_list_icon(uint8_t *g, int ncols)
{
    for (int i = 0; i < 200; i++)
        g[i] = 0;
    struct canvas c = { g, 10, 20 };
    draw_repeat(&c, 2, 3, 16, 6, 12, ncols);
}

static void add_glyphs(void *disp)
{
    void *hal = *(void **)disp;
    void (*upload)(void *, const void *, uint32_t, uint32_t) = (*(void ***)hal)[0x30 / 4];
    volatile uint8_t *m = (volatile uint8_t *)FONT4_ENTRY;     /* widths[128], fmt, stride, w, h */
    int first = 0;
    while (first < 128 && !m[first])
        first++;
    int stride = *(volatile uint32_t *)(m + 132), w = *(volatile uint32_t *)(m + 136);
    int h = *(volatile uint32_t *)(m + 140);
    uint8_t *buf = GLYPH_BUF;
    if (first <= PLAY_LEFT && stride > 0 && stride <= 16 && w >= 12 && h >= 20 && h <= 40) {
        uint32_t len = (uint32_t)(stride * h);
        static const uint8_t pairs[2][3] = { { ICON_LEFT, ICON_RIGHT, 16 }, { ARMED_LEFT, ARMED_RIGHT, 7 } };
        for (int p = 0; p < 2; p++) {
            for (uint32_t k = 0; k < (uint32_t)stride * 64 + len; k++)
                buf[k] = 0;
            struct canvas c = { buf, stride, w };
            draw_repeat(&c, w - 10, 4, 18, 8, 13, pairs[p][2]);   /* where the loop icon is */
            upload(hal, buf, (len + 3) & ~3u, FONT4_BITMAP + (uint32_t)(pairs[p][0] - first) * len);
            upload(hal, buf + stride * 64, (len + 3) & ~3u, FONT4_BITMAP + (uint32_t)(pairs[p][1] - first) * len);
            m[pairs[p][0]] = m[PLAY_LEFT];
            m[pairs[p][1]] = m[PLAY_RIGHT];
        }
        for (int k = 0; k < 8; k++)         /* the widths in RAM_G too: 0x5c..0x63 and 0x78..0x7f */
            buf[k] = m[0x5C + k];
        upload(hal, buf, 8, FONT4_METRIC + 0x5C);
        for (int k = 0; k < 8; k++)
            buf[k] = m[0x78 + k];
        upload(hal, buf, 8, FONT4_METRIC + 0x78);
    }
    make_list_icon(buf, 16);
    upload(hal, buf, 200, ICON_RAMG);
    make_list_icon(buf, 7);
    upload(hal, buf, 200, ICON_RAMG_ARMED);
}

void mt_sl_load_fonts(void *disp, const void *table, int n)    /* disp_init */
{
    ((fonts_fn)mt_sl_next_load_fonts)(disp, table, n);
    add_glyphs(disp);
}

void mt_sl_font_set_a(void *disp, int set)
{
    ((font_set_fn)mt_sl_next_font_set_a)(disp, set);
    add_glyphs(disp);
}

void mt_sl_font_set_b(void *disp, int set)
{
    ((font_set_fn)mt_sl_next_font_set_b)(disp, set);
    add_glyphs(disp);
}

/* The Song list's icon next to the play position: (ctrl, slot, row, x, y0, row height);
 * the song data D = ctrl+0x50: +0x10 icon on, +0x11 its slot. */
static void list_icon(void *ctrl, int slot, int row, int x, int y0, int rowh, uint32_t next)
{
    volatile struct sl_state *s = state();
    volatile uint8_t *D = *(uint8_t *volatile *)((uint8_t *)ctrl + 0x50);
    if (s->loop && song_playing() && D[0x10] && (int8_t)D[0x11] == slot) {
        note_entered();
        eve_bitmap_source(s->entered ? ICON_RAMG : ICON_RAMG_ARMED);
        eve_bitmap_layout(2, 10, 20);
        eve_bitmap_size(0, 0, 0, 20, 20);
        eve_begin(1);                       /* BITMAPS */
        eve_vertex2f(x, y0 + 1 + row * rowh - 10);
        eve_end();
        return;
    }
    ((list_icon_fn)next)(ctrl, slot, row, x, y0, rowh);
}

#define LIST_ICON_HOOK(name, chain)                                                      \
    void name(void *ctrl, int slot, int row, int x, int y0, int rowh)                    \
    {                                                                                    \
        list_icon(ctrl, slot, row, x, y0, rowh, chain);                                  \
    }
LIST_ICON_HOOK(mt_sl_list_icon_a, mt_sl_next_list_icon_a)
LIST_ICON_HOOK(mt_sl_list_icon_b, mt_sl_next_list_icon_b)
LIST_ICON_HOOK(mt_sl_list_icon_c, mt_sl_next_list_icon_c)
LIST_ICON_HOOK(mt_sl_list_icon_d, mt_sl_next_list_icon_d)

/* status bar: sprintf(buf, "%3im:%02is %2s P%i.%03i%2s", min, s, icon, pattern, pos, suffix) */
int mt_sl_status_sprintf(char *buf, const char *fmt, int min, int sec, const char *icon, int pat, int pos,
                         const char *suffix)
{
    volatile struct sl_state *s = state();
    if (s->loop && song_playing()) {
        note_entered();
        icon = s->entered ? ICON_STR : ARMED_STR;
    }
    return ((sprintf8_fn)mt_sl_next_status_sprintf)(buf, fmt, min, sec, icon, pat, pos, suffix);
}

/* ---- softkey labels ------------------------------------------------------------------- */

static void set_label(uint8_t st, const char *l1, const char *l2, const char *r1, const char *r2)
{
    if (SONG_LABEL_STATE == st)
        return;
    disp_set_text(FW_DISPLAY, SONG_SK0_ID, l1);
    disp_set_text2(FW_DISPLAY, SONG_SK0_ID, l2);
    disp_set_text(FW_DISPLAY, SONG_SK0B_ID, r1);
    disp_set_text2(FW_DISPLAY, SONG_SK0B_ID, r2);
    disp_refresh(FW_DISPLAY, SONG_SK0_ID);
    disp_refresh(FW_DISPLAY, SONG_SK0B_ID);
    SONG_LABEL_STATE = st;
}

static const uint32_t *softkey_colors(void)    /* stock: grey (theme+0x54) with Rec off, active (+0x44) on */
{
    return (const uint32_t *)(FW_THEME + ((slots_locked() || !rec_on()) ? 0x54 : 0x44));
}

static void softkeys_recolor(void)
{
    for (int i = 0; i < 4; i++) {
        int id = SONG_SK_ID(i);
        disp_set_colors(FW_DISPLAY, id, softkey_colors());
        disp_refresh(FW_DISPLAY, id);
    }
    disp_sync_refresh(FW_DISPLAY);
}

void mt_sl_softkey_colors(void *disp, int id, const uint32_t *colors)   /* Rec on/off, Song opened */
{
    (void)colors;
    ((colors_fn)mt_sl_next_softkey_colors)(disp, id, softkey_colors());
}

/* The play icon in the pattern column: song +0x18 on, +0x19 position, +0x1a kind (1 play,
 * 2 loop); the screen keeps the position it shows in +0x122 (0xffff when opened). With the
 * perform flag on, the Song update (from 0x600fdf5e) takes over the play position only with
 * Rec off; with Rec on (needed for a selection) +0x122 stays and the icon goes to that old
 * position (0xff after opening: no icon). This hook runs just before that code and takes the
 * position over, as the update does without Performance. */
#define SONG_PLAYHEAD_SHOWN REG16(FW_SONG + 0x122)
#define SONG_ICON_ON        REG8(FW_SONG + 0x18)
#define SONG_ICON_POS       REG8(FW_SONG + 0x19)
#define song_set_play_icon  FN(0x600FF448, void (*)(void *song, int loop_icon, int pos))

static void follow_playhead(void *song)
{
    if (!SEQ_PERFORM || stopped() || !rec_on())
        return;
    uint8_t pos = SONG_POS;
    SONG_PLAYHEAD_SHOWN = pos;
    if (!SONG_ICON_ON || SONG_ICON_POS != pos)
        song_set_play_icon(song, !SEQ_SONG_MODE, pos);
}

void mt_sl_labels(void *song)               /* the Song update, every 100 ms */
{
    volatile struct sl_state *s = state();
    follow_playhead(song);
    if (stopped())
        s->wrap_pending = 0;                /* a decision taken while playing holds only then */
    if (s->sel && !s->loop && !(SONG_FOCUS == 1 && rec_on())) {
        s->sel = 0;                         /* Rec off or another focus: no selection (not during the loop) */
        song_show_cursor(song);
    }
    if (!(SONG_FOCUS == 1 && rec_on()))
        s->snap = 0;
    uint8_t locked = (uint8_t)slots_locked();
    if (locked != s->dimmed) {
        s->dimmed = locked;
        softkeys_recolor();
    }
    if (s->loop && song_playing()) {
        set_label(LABEL_CONTINUE, "Continue", "Song", "Stop", "");
        return;
    }
    if (selection_usable(s) && stopped()) {
        set_label(LABEL_PLAY_SEL, "Play", "Selection", "Play", "Song");
        return;
    }
    if (selection_usable(s) && song_playing()) {
        set_label(LABEL_PLAY_SEL_RUN, "Play", "Selection", "Stop", "");
        return;
    }
    ((labels_fn)mt_sl_next_labels)(song);
}

/* ---- softkey 0 and Shift + Play ------------------------------------------------------- */

void mt_sl_softkey0(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)
{
    volatile struct sl_state *s = state();
    if (s->loop && song_playing()) {        /* Continue Song: play the block out, then on */
        s->loop = 0;
        s->sel = 0;
        s->snap = 0;
        if (SEQ_PERFORM) {                  /* as stock Continue Song: Performance off at the next pattern end */
            audio_irq_disable();
            perform_continue_song(FW_PERFORM);
            audio_irq_enable();
        }
        SONG_CURSOR = SONG_POS;             /* the red frame on the position playing now */
        song_show_cursor(SONG);
        statusbar_invalidate();
        mt_sl_labels(SONG);
        return;
    }
    if (selection_usable(s) && song_playing()) {    /* playing: play on, repeat the block at its end */
        s->wrap_pending = 0;
        s->entered = 0;
        s->loop = 1;
        note_entered();
        if (SONG_POS > s->last)             /* the block behind the play position: to its start at the end */
            s->entered = 1;                 /* of this pattern (as queueing), |: :| at once */
        statusbar_invalidate();
        mt_sl_labels(SONG);
        return;
    }
    if (selection_usable(s) && stopped()) {         /* stopped: from the block's start */
        SONG_CURSOR = s->first;
        s->wrap_pending = 0;
        s->entered = 1;
        song_play_song();                   /* as Play Song, from the cursor; it stops first (mt_sl_stop) */
        s->loop = 1;
        SONG_CURSOR = s->end;               /* the cursor back on the edge Shift + Up/Down moves */
        song_show_cursor(SONG);
        statusbar_invalidate();
        mt_sl_labels(SONG);
        return;
    }
    ((callback_fn)mt_sl_next_softkey0)(r0, r1, r2, r3);
}

void mt_sl_play(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)   /* Play in Song: Shift + Play = softkey 0 */
{
    if (!SONG_POPUP && shift_held()) {
        mt_sl_softkey0(r0, r1, r2, r3);
        return;
    }
    ((callback_fn)mt_sl_next_play)(r0, r1, r2, r3);
}

/* ---- selecting ------------------------------------------------------------------------ */

static void note_entered(void)              /* the play position in the block: the loop follows it from now */
{
    volatile struct sl_state *s = state();
    uint8_t pos = SONG_POS;
    if (s->loop && s->sel && pos >= s->first && pos <= s->last)
        s->entered = 1;
}

/* Up (-1) / Down (+1) in the pattern column (the jog wheel too, with its step).
 *  - Shift: selecting, downwards only. The anchor is the top: Shift + Down extends the
 *    bottom, Shift + Up pulls it back (to one row); the block never grows upwards (the block
 *    doesn't show which edge moves).
 *  - During the loop without Shift: the whole block moves (same size), within the song; with
 *    the play position outside it, the loop goes to its new start at the pattern end.
 *  - Otherwise: no selection, the stock cursor move. */
static void slot_move(void *song, int delta)
{
    volatile struct sl_state *s = state();
    note_entered();
    int slots = SONG_SLOTS;
    if (SONG_FOCUS == 1 && rec_on() && shift_held() && slots) {
        if (!s->sel)
            s->anchor = s->end = SONG_CURSOR;
        int end = s->end + delta;
        if (end < s->anchor)
            end = s->anchor;
        if (end >= slots)
            end = slots - 1;
        s->first = s->anchor;
        s->last = s->end = (uint8_t)end;
        s->sel = s->first != s->last || s->loop;    /* during the loop one row stays a block */
        s->snap = 1;
        SONG_CURSOR = (uint8_t)end;
        song_show_cursor(song);
        return;
    }
    if (s->loop) {
        if (s->sel && SONG_FOCUS == 1 && slots) {
            int size = s->last - s->first;
            int first = s->first + delta;
            if (first > slots - 1 - size)
                first = slots - 1 - size;
            if (first < 0)
                first = 0;
            s->first = s->anchor = (uint8_t)first;
            s->last = s->end = (uint8_t)(first + size);
            s->snap = 1;
            SONG_CURSOR = s->end;
            song_show_cursor(song);
        }
        return;
    }
    s->sel = 0;                             /* without Shift: no selection */
    s->snap = 0;
    song_slot_move(song, delta);
}

void mt_sl_up(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)
{
    if (!SONG_POPUP && SONG_FOCUS == 1) {
        slot_move(SONG, -1);
        return;
    }
    ((callback_fn)mt_sl_next_up)(r0, r1, r2, r3);
}

void mt_sl_down(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)
{
    if (!SONG_POPUP && SONG_FOCUS == 1) {
        slot_move(SONG, 1);
        return;
    }
    ((callback_fn)mt_sl_next_down)(r0, r1, r2, r3);
}

/* jog (delta in r0): in the pattern column it moves the cursor, or with Shift changes
 * the pattern */
void mt_sl_jog(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)
{
    if (!SONG_POPUP && SONG_FOCUS == 1) {
        if (!shift_held()) {
            slot_move(SONG, (int32_t)r0);
            return;
        }
        if (slots_locked())
            return;
    }
    ((callback_fn)mt_sl_next_jog)(r0, r1, r2, r3);
}

/* ---- the block's frame ---------------------------------------------------------------- */

/* Called from the song control's drawing just before the cursor frame: y0 = the frame's
 * top. Returns (y1 - 27) << 16 | y0; the firmware draws the frame. The block belongs to
 * the slots, not the cursor: while the cursor glides to another row the block stays,
 * while the list scrolls it moves with the numbers. */
static uint32_t block_y(void *ctrl, int y0, int sel_a, int sel_b)
{
    int first = CTRL_FIRST(ctrl), vis = SONG_VISIBLE;
    int top = CTRL_Y(ctrl) + 0x3E, anim = CTRL_ANIM(ctrl);
    int base = (y0 - CTRL_ROW(ctrl) * ROW_H == top) ? top - anim : top;
    int y_a = base + (sel_a - first) * ROW_H, y_b = base + (sel_b - first + 1) * ROW_H;
    if (y_a < top)
        y_a = top;
    if (y_b > top + vis * ROW_H)
        y_b = top + vis * ROW_H;
    if (y_a >= y_b)
        return 0;
    return (uint16_t)y_a | ((uint32_t)(uint16_t)(y_b - ROW_H) << 16);
}

#define GREY_BLOCK_RGB      0x333333u

static void grey_block(void *ctrl, int y0, int x0, int x1)  /* the looping block, focus in the grid */
{
    volatile struct sl_state *s = state();
    uint32_t r = block_y(ctrl, y0, s->first, s->last);
    if (!r)
        return;
    int ya = (int16_t)(r & 0xFFFF), yb = (int16_t)(r >> 16) + ROW_H;
    eve_color_rgb(GREY_BLOCK_RGB);
    eve_line_width(0x18);
    eve_begin(4);                           /* LINE_STRIP, as the cursor frame */
    eve_vertex2f(x0, ya);
    eve_vertex2f(x1, ya);
    eve_vertex2f(x1, yb);
    eve_vertex2f(x0, yb);
    eve_vertex2f(x0, ya);
    eve_end();
    const uint32_t *tbl = *(const uint32_t *const *)((uint8_t *)ctrl + 0x28);   /* the frame's color back */
    eve_color_rgb(SONG_D8 == 0 ? tbl[1] : (SONG_REC == 1 ? tbl[5] : tbl[9]));
}

uint32_t mt_sl_frame_calc(void *ctrl, int y0, int x0, int x1)
{
    volatile struct sl_state *s = state();
    uint32_t same = (uint16_t)y0 | ((uint32_t)(uint16_t)y0 << 16);
    int sel_a, sel_b;
    if (SONG_FOCUS != 1) {
        if (s->loop && s->sel)
            grey_block(ctrl, y0, x0, x1);
        return same;
    }
    if (s->sel && s->last < SONG_SLOTS) {
        sel_a = s->first;
        sel_b = s->last;
    } else if (s->snap) {                   /* shrunk to one row: not the gliding cursor */
        sel_a = sel_b = SONG_CURSOR;
    } else {
        return same;
    }
    uint32_t r = block_y(ctrl, y0, sel_a, sel_b);
    return r ? r : same;
}

/* bl eve_line_width at 0x601086fa (frame still: y0 in r7, y1's copy in sb) and 0x601088a4
 * (frame gliding: y0 in sl, y1 = r7 + 27). r0 = the width, r4 = the control; r1-r3 and
 * ip are free there. */
__attribute__((naked, used)) void mt_sl_frame_a(void)
{
    __asm__ volatile(
        "push {r0, lr}\n\t"
        "mov r0, r4\n\t"
        "mov r1, r7\n\t"
        "mov r2, r8\n\t"                    /* x0 */
        "add r3, sl, r5\n\t"                /* x1 */
        "bl mt_sl_frame_calc\n\t"
        "sxth r7, r0\n\t"
        "lsrs r0, r0, #16\n\t"
        "mov sb, r0\n\t"
        "pop {r0, lr}\n\t"
        "ldr ip, =mt_sl_next_frame_a\n\t"
        "ldr ip, [ip]\n\t"
        "bx ip\n\t"
        ".ltorg");
}

__attribute__((naked, used)) void mt_sl_frame_b(void)
{
    __asm__ volatile(
        "push {r0, lr}\n\t"
        "mov r0, r4\n\t"
        "mov r1, sl\n\t"
        "mov r2, r8\n\t"                    /* x0 */
        "add r3, r5, sb\n\t"                /* x1 */
        "bl mt_sl_frame_calc\n\t"
        "sxth r1, r0\n\t"
        "mov sl, r1\n\t"
        "lsrs r0, r0, #16\n\t"
        "mov r7, r0\n\t"
        "pop {r0, lr}\n\t"
        "ldr ip, =mt_sl_next_frame_b\n\t"
        "ldr ip, [ip]\n\t"
        "bx ip\n\t"
        ".ltorg");
}

/* ---- locked while a block is selected or looping --------------------------------------- */

void mt_sl_undo(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)
{
    if (!slots_locked())
        ((callback_fn)mt_sl_next_undo)(r0, r1, r2, r3);
}

void mt_sl_add(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)
{
    if (!slots_locked())
        ((callback_fn)mt_sl_next_add)(r0, r1, r2, r3);
}

void mt_sl_delete(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)
{
    if (!slots_locked())
        ((callback_fn)mt_sl_next_delete)(r0, r1, r2, r3);
}

void mt_sl_delete_key(uint32_t r0, uint32_t r1, uint32_t r2, uint32_t r3)  /* pattern column: Delete Slot */
{
    if (!SONG_POPUP && SONG_FOCUS == 1 && slots_locked())
        return;
    ((callback_fn)mt_sl_next_delete_key)(r0, r1, r2, r3);
}

#define CHANGE_HOOK(name, chain)                                                         \
    void name(void *song, int delta)                                                     \
    {                                                                                    \
        if (!slots_locked())                                                             \
            ((change_fn)chain)(song, delta);                                             \
    }
CHANGE_HOOK(mt_sl_change_a, mt_sl_next_change_a)
CHANGE_HOOK(mt_sl_change_b, mt_sl_next_change_b)
CHANGE_HOOK(mt_sl_change_c, mt_sl_next_change_c)
CHANGE_HOOK(mt_sl_change_d, mt_sl_next_change_d)

const char mt_version_fmt[] = "%d.%d.%d-" SONGLOOP_VERSION;
