/*
 * interpolate: CC and Macro ramps between steps (Polyend Tracker+ firmware 1.2.0).
 *
 * Stock firmware applies a CC FX (a..f) once, at the start of its step: on a MIDI
 * track it sends a MIDI CC, on a synth track (S1..S3) it sets that Macro. With this
 * patch, the value ramps towards the next step on that track with the same letter: CC A
 * 0 on step 1 and CC A 127 on step 17 becomes a 16-step sweep. The steps in between need
 * no FX. MIDI tracks and synth tracks each have their own Off/On setting.
 *
 * The ramp. mt_ip_seq_advance replaces the call of seq_advance in the audio block
 * (0x600aa7f8, app_audio_block): it runs the sequencer as before, then the ramp pass,
 * in the same interrupt, once per block (128 frames, about 2.9 ms). Per track it
 * keeps, for each CC letter, the step the ramp starts from (k, the last step at or
 * before the current one with that letter) and the step it goes to (j, the next one
 * after it). When the track moves on, its ramps move on with it; the full look-up
 * (one pass through the track's steps) runs again for at most 2 tracks per block, so
 * a pattern of 16 tracks stays cheap. A ramp needs:
 *  - both steps on the same MIDI channel instrument (48..63) or the same synth
 *    (64..66); sample instruments get no ramps;
 *  - on a MIDI channel, a CC number for that letter (not program change or pressure);
 *  - different values.
 * The value is from + (to - from) * position, with position = (steps since k + (micro
 * step - 1) / 48) / (steps from k to j). The stock code still sends the value of each
 * CC step at its start. In Pattern mode the search wraps around the pattern (from the
 * last CC back to the first); in Song mode, or with a pattern queued, the value holds
 * after the last CC of the pattern, because the next pattern's steps aren't loaded
 * yet. Selection playback (the Pattern screen's Preview) ramps as normal playback
 * does, from the whole pattern's CC steps: seq_play_selection (0x600ac3d0) clears
 * every track's enabled flag and sets it again only for the selected tracks, and
 * tracks that aren't enabled send nothing. Performance mode gets no ramps.
 *
 * Sending. Through the stock midi_send_cc (ITCM 0xacc), so the MIDI out setting and
 * rendering are respected. Only when the 7-bit value changes. A capped number per
 * block, shared round-robin between all ramps: 1 when MIDI out includes the DIN jack
 * (about 345 CCs a second, a third of what the cable carries) and 4 for USB only. None
 * while the jack's send ring holds more than 128 bytes, because the stock ring has no
 * check for a full buffer. Muted and disabled tracks send nothing.
 *
 * Synths. On S1..S3 the stock code hands these FX to synth_apply_fx_q (ITCM 0x5070)
 * with the step's FX column, which turns the value into the Macro's range and stores
 * it as the synth's pending parameter (the engine picks it up when it renders). The
 * ramp pass makes the same call with the ramp value, at most 8 per block, and again
 * after every step change, in case a note resets the Macro. The render loop (rendering
 * to a file, 0x600bc27a) calls seq_advance through its own call site; that one is
 * hooked too, so rendered synth tracks ramp the same way (MIDI stays silent while
 * rendering, as midi_send_cc checks for it).
 *
 * The settings. Project Settings gets an "FX Interpolation" folder with two Off/On
 * items, both Off by default: "MIDI CC" (MIDI channel instruments) and "Synths"
 * (S1..S3, also when rendering). The folder and item nodes come from the firmware's
 * heap, as humanize's do. Each saved project keeps them in <project folder>/
 * interpolate.bin, read and written from the Pattern and Config screens' update
 * methods, only while the file manager is idle, in the same way as humanize: another
 * project gets Off at once, then its file; Save As (a new project's first save is one;
 * marked at the save request's name copy, 0x60114eaa) keeps them for the new name and
 * writes them there, as the firmware copies only its own files; a new project ("New
 * Project", no path) writes nothing until it has a folder.
 *
 * Shared hooks. The Project Settings folder, the menu constructor's last item, the
 * two update methods and the two seq_advance calls are hooked through chain words
 * (mt_ip_next_*) that tools/patch_ptf.py fills with what the site pointed to before,
 * so other patches can hook them too.
 *
 * Diagnostic build (-DIP_DIAG, version "ipD"): Project Settings also gets an "Interp
 * debug" folder of read-only items that show what the ramp pass saw while playing:
 * how often it ran, what stopped it, the pattern length, the cursor track's step,
 * instrument, CC A value and number, its ramp, the MIDI out setting and what was sent.
 */
#include <stdint.h>

#ifndef INTERPOLATE_VERSION
#define INTERPOLATE_VERSION "ip8"
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
#define FW_SETTINGS         0x2002061Cu     /* global settings: +0x2c MIDI out */
#define FW_CC_NUMBERS       0x20204AB3u     /* + 0x10 * (channel - 1) + letter: CC number */
#define FW_JACK_RING_PTR    0x2000C69Cu     /* -> jack send ring: +0 write, +4 read index */

#define FW_MIDI_SEND_CC     0x00000ACDu     /* ITCM: (cc, value, channel 1..16, synth) */
#define FW_TRACK_MUTED      0x00003995u     /* ITCM: (seq, track) -> muted */
#define FW_SYNTH_APPLY_FX   0x00005071u     /* ITCM: (engine, type, value, column, synth 0..2) */
#define FW_ENGINE           0x20017B08u     /* audio engine */
#define FW_MENU_ITEM        0x601183E9u     /* (node, parent, label, help, type, descriptor) */
#define FW_MALLOC           0x600A3589u     /* (size) */
#define FW_SNPRINTF         0x6016712Du
#define FW_OFF_ON           0x60060B88u     /* option names {"Off", "On"} */
#define FW_ED               0x20022770u     /* Pattern screen editor state: +0x1e4 cursor track */

/* sequencer object */
#define SEQ_PATTERN         0x14u           /* -> play pattern data (.mtp layout) */
#define SEQ_SONG_MODE       0x1Cu
#define SEQ_PERFORMANCE     0x1Du
#define SEQ_SELECTION      0x1Eu           /* selection playback (Preview) */
#define SEQ_SELECTION_LOOP  0x1Fu
#define SEQ_QUEUED          0x28u
#define SEQ_PLAYING         0x2Au
#define SEQ_LENGTH          0x36u           /* int16 pattern length override, -1 = none */
#define SEQ_COUNT_IN        0x6F8u
#define SEQ_TRACK(t)        (0x68u * (t))   /* + the track offsets below */
#define TRK_ENABLED         0x7Du
#define TRK_MICRO           0x86u           /* uint16: next micro-step to process, 1..48 */
#define TRK_STEP            0x88u           /* uint16: current step */

/* pattern data: 0x1c-byte header, then 16 tracks of 0x301 bytes (last-step byte,
 * then 128 steps of note, instrument, fx1 type, fx1 value, fx2 type, fx2 value) */
#define PAT_LAST_STEP       0x1Cu
#define PAT_STEPS           0x1Du
#define PAT_TRACK_SIZE      0x301u
#define PAT_TRACKS          16u
#define PAT_MAX_STEPS       128u
#define INSTR_MIDI_FIRST    48u             /* 48..63: MIDI channels 1..16 */
#define INSTR_MIDI_LAST     63u
#define INSTR_SYNTH_FIRST   64u             /* 64..66: S1..S3 */
#define INSTR_SYNTH_LAST    66u
#define MICRO_STEPS         48u
#define LETTERS             6u              /* CC a..f */

#define MIDI_OUT_JACK_LIMIT 1u              /* ramp CCs per block with the jack on */
#define MIDI_OUT_USB_LIMIT  4u              /* ... with USB only */
#define JACK_RING_SIZE      0x200u
#define JACK_RING_MAX       128u            /* no ramp CCs while the ring holds more */
#define SCANS_PER_BLOCK     2u              /* full look-ups per audio block */
#define SYNTH_LIMIT         8u              /* synth Macro updates per block */

typedef void (*void_fn)(void);
typedef void (*send_cc_fn)(uint32_t cc, uint32_t value, uint32_t channel, uint32_t synth);
typedef int (*muted_fn)(void *seq, uint32_t track);
typedef void (*synth_fx_fn)(void *engine, uint32_t type, uint32_t value, uint32_t column, uint32_t synth);
typedef void *(*folder_fn)(void *node, void *parent, const char *label, uint32_t capacity);
typedef void *(*item_fn)(void *node, void *parent, const char *label, const char *help,
                         uint32_t type, const void *descriptor);
typedef void *(*malloc_fn)(uint32_t size);
typedef int (*snprintf_fn)(char *buf, uint32_t size, const char *fmt, ...);
typedef char *(*strncpy_fn)(char *dst, const char *src, uint32_t n);
typedef void (*update_fn)(void *screen, void *a1, void *a2, void *a3);
typedef uint32_t (*project_update_fn)(void *screen, void *a1, void *a2, void *a3);
typedef void (*advance_fn)(void *seq, float seconds);

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_ip_next_seq_advance);         /* seq_advance (veneer 0x600aa859) */
CHAIN_WORD(mt_ip_next_render_advance);      /* seq_advance in the render loop (veneer 0x600bc2d9) */
CHAIN_WORD(mt_ip_next_folder);              /* Project Settings: cfg_menu_folder 0x60118395 */
CHAIN_WORD(mt_ip_next_last_item);           /* last item: cfg_menu_item 0x601183e9 */
CHAIN_WORD(mt_ip_next_pattern_update);      /* Pattern screen update 0x600e1f99 */
CHAIN_WORD(mt_ip_next_config_update);       /* Config screen update 0x600c6475 */
CHAIN_WORD(mt_ip_next_save_name);           /* strncpy 0x601673d7 (Save As: the new name) */
CHAIN_WORD(mt_ip_next_project_update);      /* project screen update 0x600ead59 */

/* CC FX types for the letters a..f, and the other way round: FX type -> its letter as a
 * bit (0..5), for every byte value, as the look-up reads every step of a track and a
 * step then costs two table reads */
static const uint8_t cc_type[LETTERS] = {8, 9, 10, 11, 12, 37};
static const uint8_t cc_bit[256] = {[8] = 1, [9] = 2, [10] = 4, [11] = 8, [12] = 16, [37] = 32};

/* ---- state (.mt_ram: neither loaded nor zeroed; the patcher places it) ----------- */

#define IP_MAGIC            0x36504D4Du     /* "MMP6": the state's layout of ip6 */
#define SAVE_DELAY          25u             /* screen updates between a change and the save */
#define NO_STEP             0xFFFFu
#define NOT_SENT            0xFFu

struct ramp {
    uint16_t from_step;                     /* k */
    uint8_t active;
    uint8_t cc;                             /* MIDI CC number */
    uint8_t channel;                        /* MIDI channel 1..16, 0 for a synth */
    uint8_t from, to;                       /* values 0..127 */
    uint8_t last;                           /* last value sent for it (NOT_SENT: none) */
    uint8_t synth;                          /* 0: MIDI; 1..3: S1..S3 */
    uint8_t column;                         /* the FX column at k (synths): 0 FX1, 1 FX2 */
    uint16_t elapsed;                       /* steps from k to the current step */
    uint16_t length;                        /* steps from k to j */
};

struct track_state {
    uint16_t step;                          /* the step its ramps are at */
    uint8_t scan;                           /* the full look-up is due */
    uint8_t pad;
    const uint8_t *pattern;                 /* ... and the pattern buffer */
    struct ramp ramp[LETTERS];
};

#ifdef IP_DIAG
#define DIAG_LINES          12u
#define DIAG_TEXT           20u
struct ip_diag {
    uint32_t calls, passed, sent, scans;
    uint8_t why, last_sent, out, budget;
    uint8_t playing, count_in, perf, sel_once, sel_loop, song, queued, last;
    int16_t override;
    uint16_t fill;                          /* 0xffff: no ring pointer */
    uint8_t track, step, instr, cc, enabled, muted, active, from, to, pad;
    int16_t value;
    uint16_t elapsed, length;
    char text[DIAG_LINES][DIAG_TEXT];
};
#endif

struct ip_state {
    uint32_t magic;
    uint32_t session;                       /* project name + path the value belongs to */
    uint8_t value;                          /* MIDI CC: 0 Off, 1 On (the menu edits it) */
    uint8_t saved;                          /* what interpolate.bin holds for it */
    uint8_t saved_valid;
    uint8_t load;                           /* the project's file is still to be read */
    uint8_t wait;                           /* updates left before saving */
    uint8_t running;                        /* the ramps below are valid */
    uint8_t next_ramp;                      /* round-robin start for sending, 0..95 */
    uint8_t next_scan;                      /* round-robin start for look-ups, 0..15 */
    uint8_t synth;                          /* Synths: 0 Off, 1 On (the menu edits it) */
    uint8_t saved_synth;                    /* what interpolate.bin holds for it */
    uint8_t pad[2];
    uint32_t save_as;                       /* the session a Save As moves the settings to; 0: none */
    struct track_state track[PAT_TRACKS];
#ifdef IP_DIAG
    struct ip_diag diag;
#endif
};
static struct ip_state ip_state_ram __attribute__((section(".mt_ram")));
#define IP (&ip_state_ram)

void *memset(void *dst, int c, unsigned n);
void *memset(void *dst, int c, unsigned n)
{
    uint8_t *d = dst;
    while (n--)
        *d++ = (uint8_t)c;
    return dst;
}

static void ip_init_if_needed(void)         /* RAM outside .bss holds garbage at power-on */
{
    if (IP->magic == IP_MAGIC)
        return;
    IP->value = 0;
    IP->synth = 0;
    IP->session = 0;
    IP->saved_valid = 0;
    IP->load = 0;
    IP->wait = 0;
    IP->save_as = 0;
    IP->running = 0;
    IP->next_ramp = 0;
    IP->next_scan = 0;
    IP->magic = IP_MAGIC;
}

/* ---- the ramps (audio interrupt) -------------------------------------------------- */

static uint32_t rd16(const uint8_t *p)
{
    return p[0] | p[1] << 8;
}

static const uint8_t *step_at(const uint8_t *pattern, uint32_t t, uint32_t s)
{
    return pattern + PAT_STEPS + PAT_TRACK_SIZE * t + 6 * s;
}

/* The value of CC letter `l` on this step, or -1 (FX1 wins, as the stock code applies
 * FX2 first and then FX1). */
static int cc_value(const uint8_t *step, uint32_t l)
{
    if (step[2] == cc_type[l])
        return step[3];
    if (step[4] == cc_type[l])
        return step[5];
    return -1;
}

/* The CC letters on this step, as bits 0..5. */
static inline __attribute__((always_inline)) uint32_t cc_letters(const uint8_t *step)
{
    return cc_bit[step[2]] | cc_bit[step[4]];
}

/* Look up track t's ramps for its current step `cur` (0..last). */
static void find_ramps(const uint8_t *pattern, uint32_t t, uint32_t cur, uint32_t last, int wrap)
{
    struct track_state *ts = &IP->track[t];
    uint32_t n = last + 1;
    int16_t back[LETTERS], fwd[LETTERS];    /* distance to k (0..n-1) and to j (1..n), -1 none */
    for (uint32_t l = 0; l < LETTERS; l++)
        back[l] = fwd[l] = -1;
    /* One pass over the steps, each read once: a track rarely has all six letters, so
     * searching each way until every letter turns up would read the whole pattern twice.
     * Wrapping, the pass runs from the step after `cur` round to `cur` itself (distance
     * d = 1..n): a letter's first step is j (fwd = d), its last is k (back = n - d).
     * Not wrapping, it runs from the first step to the last: k is the last step at or
     * before `cur`, j the first after it. */
    if (wrap) {
        const uint8_t *p = step_at(pattern, t, cur);
        const uint8_t *first = step_at(pattern, t, 0), *end = step_at(pattern, t, last);
        for (uint32_t d = 1; d <= n; d++) {
            p = p == end ? first : p + 6;
            for (uint32_t m = cc_letters(p); m; m &= m - 1) {
                uint32_t l = (uint32_t)__builtin_ctz(m);
                if (fwd[l] < 0)
                    fwd[l] = (int16_t)d;
                back[l] = (int16_t)(n - d);
            }
        }
    } else {
        const uint8_t *p = step_at(pattern, t, 0);
        for (uint32_t s = 0; s <= last; s++, p += 6) {
            for (uint32_t m = cc_letters(p); m; m &= m - 1) {
                uint32_t l = (uint32_t)__builtin_ctz(m);
                if (s <= cur)
                    back[l] = (int16_t)(cur - s);
                else if (fwd[l] < 0)
                    fwd[l] = (int16_t)(s - cur);
            }
        }
    }
    for (uint32_t l = 0; l < LETTERS; l++) {
        struct ramp *r = &ts->ramp[l];
        int was = r->active;
        uint32_t old_k = r->from_step, old_cc = r->cc, old_channel = r->channel, old_synth = r->synth;
        r->active = 0;
        if (back[l] < 0 || fwd[l] < 0)
            continue;
        uint32_t k = (uint32_t)back[l] <= cur ? cur - (uint32_t)back[l] : cur + n - (uint32_t)back[l];
        uint32_t j = cur + (uint32_t)fwd[l] <= last ? cur + (uint32_t)fwd[l] : cur + (uint32_t)fwd[l] - n;
        const uint8_t *sk = step_at(pattern, t, k), *sj = step_at(pattern, t, j);
        uint32_t instr = sk[1], channel = 0, cc = 0, synth = 0;
        if (sj[1] != instr)
            continue;
        if (instr >= INSTR_MIDI_FIRST && instr <= INSTR_MIDI_LAST) {
            channel = instr - INSTR_MIDI_FIRST + 1;
            cc = *(const volatile uint8_t *)(FW_CC_NUMBERS + 0x10u * (channel - 1) + l);
            if (cc > 127)
                continue;
        } else if (instr >= INSTR_SYNTH_FIRST && instr <= INSTR_SYNTH_LAST) {
            synth = instr - INSTR_SYNTH_FIRST + 1;
        } else {
            continue;
        }
        int from = cc_value(sk, l), to = cc_value(sj, l);
        if (from > 127 || to > 127 || from == to)
            continue;
        r->from_step = (uint16_t)k;
        r->cc = (uint8_t)cc;
        r->channel = (uint8_t)channel;
        r->synth = (uint8_t)synth;
        r->column = sk[2] == cc_type[l] ? 0 : 1;
        r->from = (uint8_t)from;
        r->to = (uint8_t)to;
        r->elapsed = (uint16_t)back[l];
        r->length = (uint16_t)(back[l] + fwd[l]);
        if (back[l] == 0)
            r->last = (uint8_t)from;        /* the stock code has just sent it */
        else if (!(was && old_k == k && old_cc == cc && old_channel == channel && old_synth == synth))
            r->last = NOT_SENT;             /* (the same ramp one step further keeps it) */
        r->active = 1;
    }
}

static uint32_t ramp_value(const struct ramp *r, uint32_t micro)
{
    uint32_t total = (uint32_t)r->length * MICRO_STEPS;
    uint32_t pos = (uint32_t)r->elapsed * MICRO_STEPS + (micro >= 1 && micro <= MICRO_STEPS ? micro - 1 : 0);
    if (pos > total)
        pos = total;
    int diff = (int)r->to - (int)r->from;
    /* rounded to the nearest value */
    int v = (int)r->from + (diff * (int)pos + (diff >= 0 ? (int)total / 2 : -(int)total / 2)) / (int)total;
    return (uint32_t)(v < 0 ? 0 : v > 127 ? 127 : v);
}

static uint32_t send_budget(void)
{
    uint32_t out = *(const volatile uint8_t *)(FW_SETTINGS + 0x2Cu);
#ifdef IP_DIAG
    IP->diag.out = (uint8_t)out;
    IP->diag.fill = 0xFFFF;
#endif
    if (out == 0 || out > 3)
        return 0;
    if (out == 1)
        return MIDI_OUT_USB_LIMIT;
    uint32_t ring = *(const volatile uint32_t *)FW_JACK_RING_PTR;
    if (ring >= 0x20000000u && ring < 0x20060000u && !(ring & 3)) {
        uint32_t fill = (((const volatile uint32_t *)ring)[0] - ((const volatile uint32_t *)ring)[1])
                        & (JACK_RING_SIZE - 1);
#ifdef IP_DIAG
        IP->diag.fill = (uint16_t)fill;
#endif
        if (fill > JACK_RING_MAX)
            return 0;
    }
    return MIDI_OUT_JACK_LIMIT;
}

#ifdef IP_DIAG
/* What the gates saw (only while playing, so stopping doesn't overwrite it). */
static void diag_gates(const uint8_t *seq)
{
    struct ip_diag *d = &IP->diag;
    d->calls++;
    if (!seq[SEQ_PLAYING])
        return;
    d->playing = seq[SEQ_PLAYING];
    d->count_in = seq[SEQ_COUNT_IN];
    d->perf = seq[SEQ_PERFORMANCE];
    d->sel_once = seq[SEQ_SELECTION];
    d->sel_loop = seq[SEQ_SELECTION_LOOP];
    d->song = seq[SEQ_SONG_MODE];
    d->queued = seq[SEQ_QUEUED];
    d->override = (int16_t)rd16(seq + SEQ_LENGTH);
    d->why = IP->magic != IP_MAGIC ? 'M' : !IP->value && !IP->synth ? 'V' : seq[SEQ_COUNT_IN] ? 'C'
             : seq[SEQ_PERFORMANCE] ? 'F' : !*(const uint8_t *const *)(seq + SEQ_PATTERN) ? 'N' : '-';
}

/* The cursor track, as the ramp pass sees it. */
static void diag_track(const uint8_t *seq, const uint8_t *pattern, uint32_t last)
{
    struct ip_diag *d = &IP->diag;
    uint32_t t = *(const volatile uint8_t *)(FW_ED + 0x1E4u);
    if (t >= PAT_TRACKS)
        t = 0;
    uint32_t step = rd16(seq + SEQ_TRACK(t) + TRK_STEP);
    d->last = (uint8_t)last;
    d->track = (uint8_t)t;
    d->step = (uint8_t)step;
    const uint8_t *st = step_at(pattern, t, step < PAT_MAX_STEPS ? step : 0);
    d->instr = st[1];
    d->value = (int16_t)cc_value(st, 0);
    d->cc = st[1] >= INSTR_MIDI_FIRST && st[1] <= INSTR_MIDI_LAST
            ? *(const volatile uint8_t *)(FW_CC_NUMBERS + 0x10u * (st[1] - INSTR_MIDI_FIRST)) : 0xFF;
    d->enabled = seq[SEQ_TRACK(t) + TRK_ENABLED];
    d->muted = (uint8_t)((muted_fn)FW_TRACK_MUTED)((void *)seq, t);
    const struct ramp *r = &IP->track[t].ramp[0];
    d->active = r->active;
    d->from = r->from;
    d->to = r->to;
    d->elapsed = r->elapsed;
    d->length = r->length;
}
#endif

static void ramp_block(uint8_t *seq)
{
#ifdef IP_DIAG
    diag_gates(seq);
#endif
    if (IP->magic != IP_MAGIC || (!IP->value && !IP->synth) || !seq[SEQ_PLAYING] || seq[SEQ_COUNT_IN]
        || seq[SEQ_PERFORMANCE]) {
        IP->running = 0;
        return;
    }
    const uint8_t *pattern = *(const uint8_t *const *)(seq + SEQ_PATTERN);
    if (!pattern) {
        IP->running = 0;
        return;
    }
    int16_t override = (int16_t)rd16(seq + SEQ_LENGTH);
    uint32_t last = override >= 0 ? (uint32_t)override : pattern[PAT_LAST_STEP];
    if (last >= PAT_MAX_STEPS)
        last = PAT_MAX_STEPS - 1;
    int wrap = !seq[SEQ_SONG_MODE] && !seq[SEQ_QUEUED];
    if (!IP->running) {
        for (uint32_t t = 0; t < PAT_TRACKS; t++) {
            IP->track[t].step = NO_STEP;
            IP->track[t].scan = 0;
            for (uint32_t l = 0; l < LETTERS; l++)
                IP->track[t].ramp[l].active = 0;
        }
        IP->next_ramp = 0;
        IP->next_scan = 0;
        IP->running = 1;
    }
    uint32_t n = last + 1;
    for (uint32_t t = 0; t < PAT_TRACKS; t++) {
        struct track_state *ts = &IP->track[t];
        uint32_t step = rd16(seq + SEQ_TRACK(t) + TRK_STEP);
        if (step == ts->step && pattern == ts->pattern)
            continue;
        /* The track moved on: its ramps move with it until the look-up below (a ramp
         * that has reached its target ends; the stock code sends that value). */
        int keep = pattern == ts->pattern && step <= last && ts->step <= last;
        uint32_t moved = keep ? (step + n - ts->step) % n : 0;
        for (uint32_t l = 0; l < LETTERS; l++) {
            struct ramp *r = &ts->ramp[l];
            if (!keep || (uint32_t)r->elapsed + moved >= r->length) {
                r->active = 0;
            } else {
                r->elapsed = (uint16_t)(r->elapsed + moved);
                if (r->synth)
                    r->last = NOT_SENT;     /* apply it again at the new step */
            }
        }
        ts->step = (uint16_t)step;
        ts->pattern = pattern;
        ts->scan = 1;
    }
    uint32_t scans = 0;
    for (uint32_t i = 0; i < PAT_TRACKS && scans < SCANS_PER_BLOCK; i++) {
        uint32_t t = (IP->next_scan + i) % PAT_TRACKS;
        struct track_state *ts = &IP->track[t];
        if (!ts->scan)
            continue;
        ts->scan = 0;
        if (ts->step <= last)
            find_ramps(pattern, t, ts->step, last, wrap);
        scans++;
#ifdef IP_DIAG
        IP->diag.scans++;
#endif
        IP->next_scan = (uint8_t)((t + 1) % PAT_TRACKS);
    }
    uint32_t budget = IP->value ? send_budget() : 0, synth_budget = IP->synth ? SYNTH_LIMIT : 0;
#ifdef IP_DIAG
    IP->diag.passed++;
    IP->diag.budget = (uint8_t)budget;
    diag_track(seq, pattern, last);
#endif
    uint32_t total = PAT_TRACKS * LETTERS;
    uint32_t start = IP->next_ramp < total ? IP->next_ramp : 0;
    /* (track, letter) counted up rather than divided out of the index: this loop runs
     * over all 96 ramps in every block */
    for (uint32_t i = 0, t = start / LETTERS, l = start % LETTERS; i < total && (budget || synth_budget);
         i++, l = l + 1 < LETTERS ? l + 1 : 0, t = l ? t : (t + 1) % PAT_TRACKS) {
        uint32_t n = t * LETTERS + l;
        struct ramp *r = &IP->track[t].ramp[l];
        if (!r->active || !(r->synth ? synth_budget : budget))
            continue;
        if (!seq[SEQ_TRACK(t) + TRK_ENABLED] || ((muted_fn)FW_TRACK_MUTED)(seq, t))
            continue;
        uint32_t v = ramp_value(r, rd16(seq + SEQ_TRACK(t) + TRK_MICRO));
        if (v == r->last)
            continue;
        if (r->synth) {
            ((synth_fx_fn)FW_SYNTH_APPLY_FX)((void *)FW_ENGINE, cc_type[l], v, r->column, r->synth - 1u);
            synth_budget--;
        } else {
            ((send_cc_fn)FW_MIDI_SEND_CC)(r->cc, v, r->channel, 0);
            budget--;
        }
        r->last = (uint8_t)v;
#ifdef IP_DIAG
        IP->diag.sent++;
        IP->diag.last_sent = (uint8_t)v;
#endif
        IP->next_ramp = (uint8_t)((n + 1) % total);
    }
}

/* replaces "bl seq_advance" (veneer 0x600aa858) at 0x600aa7f8 in app_audio_block (chained) */
void mt_ip_seq_advance(void *seq, float seconds)
{
    ((advance_fn)mt_ip_next_seq_advance)(seq, seconds);
    ramp_block((uint8_t *)seq);
}

/* replaces "bl seq_advance" (veneer 0x600bc2d8) at 0x600bc27a in the render loop
 * (chained), so rendered synth tracks ramp as they do when playing */
void mt_ip_render_advance(void *seq, float seconds)
{
    ((advance_fn)mt_ip_next_render_advance)(seq, seconds);
    ramp_block((uint8_t *)seq);
}

/* ---- the Config menu ------------------------------------------------------------ */

#define MENU_ITEM_SIZE      0x20u           /* fields up to +0x18 */
#define MENU_FOLDER_SIZE    0x20u           /* fields up to +0x1e */
#define FW_MENU_FOLDER      0x60118395u     /* (node, parent, label, capacity) */
#define FOLDER_ITEMS        2u              /* MIDI CC, Synths */
#define MENU_OPTIONS        4u              /* stock option item (e.g. Anti-aliasing) */

/* option item (type 4): value pointer, option count, option names, and a callback that
 * the menu's apply (cfg_menu_apply, 0x60118658) calls after storing the choice, without
 * checking it for NULL. */
struct menu_options { volatile uint8_t *value; uint32_t count; const char *const *names; void_fn on_apply; };

static void option_applied(void)            /* the ramp pass and settings_tick see the value */
{
}

static const struct menu_options interpolate_desc = {&IP->value, 2, (const char *const *)FW_OFF_ON,
                                                     option_applied};
static const struct menu_options synth_desc = {&IP->synth, 2, (const char *const *)FW_OFF_ON,
                                               option_applied};

#ifdef IP_DIAG
#define MENU_TEXT           5u              /* stock text item (e.g. Current version) */
#define PROJECT_ITEMS       2u              /* the FX Interpolation folder and the debug folder */
static const char *const diag_texts[DIAG_LINES] = {
    IP->diag.text[0], IP->diag.text[1], IP->diag.text[2], IP->diag.text[3],
    IP->diag.text[4], IP->diag.text[5], IP->diag.text[6], IP->diag.text[7],
    IP->diag.text[8], IP->diag.text[9], IP->diag.text[10], IP->diag.text[11],
};
static const char *const diag_labels[DIAG_LINES] = {
    "Passed/calls", "Stopped by", "Play cnt prf s1 s2", "Last step/override", "Song/queued",
    "Cursor trk/step", "Instrument", "CC A value/num", "Enabled/muted", "Ramp A on from>to",
    "Ramp A pos/len", "Out/budget/fill/sent",
};

static void diag_format(void)
{
    struct ip_diag *d = &IP->diag;
    snprintf_fn pr = (snprintf_fn)FW_SNPRINTF;
    pr(d->text[0], DIAG_TEXT, "%u/%u", (unsigned)d->passed, (unsigned)d->calls);
    pr(d->text[1], DIAG_TEXT, "%c", d->why ? d->why : '?');
    pr(d->text[2], DIAG_TEXT, "%u %u %u %u %u", d->playing, d->count_in, d->perf, d->sel_once, d->sel_loop);
    pr(d->text[3], DIAG_TEXT, "%u %d", d->last, d->override);
    pr(d->text[4], DIAG_TEXT, "%u %u", d->song, d->queued);
    pr(d->text[5], DIAG_TEXT, "%u %u", d->track + 1, d->step);
    pr(d->text[6], DIAG_TEXT, "%u", d->instr);
    pr(d->text[7], DIAG_TEXT, "%d %u", d->value, d->cc);
    pr(d->text[8], DIAG_TEXT, "%u %u", d->enabled, d->muted);
    pr(d->text[9], DIAG_TEXT, "%u %u>%u", d->active, d->from, d->to);
    pr(d->text[10], DIAG_TEXT, "%u/%u", d->elapsed, d->length);
    pr(d->text[11], DIAG_TEXT, "%u %u %u %u", d->out, d->budget, d->fill, (unsigned)d->sent);
}
#else
#define PROJECT_ITEMS       1u              /* the FX Interpolation folder */
#endif

/* replaces "bl 0x60118394" at 0x60118b00 (chained): Project Settings gets room for this
 * patch's items */
void *mt_ip_settings_folder(void *node, void *parent, const char *label, uint32_t capacity)
{
    return ((folder_fn)mt_ip_next_folder)(node, parent, label, capacity + PROJECT_ITEMS);
}

/* replaces the constructor's last "bl 0x601183e8" (Licenses) at 0x601193e6 (chained) */
void *mt_ip_last_item(void *node, void *parent, const char *label, const char *help,
                      uint32_t type, const void *descriptor)
{
    void *r = ((item_fn)mt_ip_next_last_item)(node, parent, label, help, type, descriptor);
    ip_init_if_needed();
    malloc_fn alloc = (malloc_fn)FW_MALLOC;
    void *folder = alloc(MENU_FOLDER_SIZE);
    if (folder) {
        ((folder_fn)FW_MENU_FOLDER)(folder, (void *)FW_PROJECT_SETTINGS, "FX Interpolation", FOLDER_ITEMS);
        void *item = alloc(MENU_ITEM_SIZE);
        if (item)
            ((item_fn)FW_MENU_ITEM)(item, folder, "MIDI CC",
                                    "On: a CC FX (a-f) on a MIDI track ramps smoothly to the next "
                                    "step with the same CC on that track, instead of jumping there.",
                                    MENU_OPTIONS, &interpolate_desc);
        item = alloc(MENU_ITEM_SIZE);
        if (item)
            ((item_fn)FW_MENU_ITEM)(item, folder, "Synths",
                                    "On: a Macro FX (a-f) on a synth track (S1-S3) ramps smoothly to "
                                    "the next step with the same Macro, also when rendering.",
                                    MENU_OPTIONS, &synth_desc);
    }
#ifdef IP_DIAG
    for (uint32_t i = 0; i < DIAG_LINES; i++)
        IP->diag.text[i][0] = 0;
    IP->diag.calls = IP->diag.passed = IP->diag.sent = IP->diag.scans = 0;
    IP->diag.why = 0;
    folder = ((malloc_fn)FW_MALLOC)(MENU_FOLDER_SIZE);
    if (folder) {
        ((folder_fn)FW_MENU_FOLDER)(folder, (void *)FW_PROJECT_SETTINGS, "Interp debug", DIAG_LINES);
        for (uint32_t i = 0; i < DIAG_LINES; i++) {
            void *it = ((malloc_fn)FW_MALLOC)(MENU_ITEM_SIZE);
            if (it)
                ((item_fn)FW_MENU_ITEM)(it, folder, diag_labels[i], "Interpolate diagnostic: what the "
                                        "ramp pass saw while playing.", MENU_TEXT, &diag_texts[i]);
        }
    }
#endif
    return r;
}

/* ---- interpolate.bin ------------------------------------------------------------- */

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

#define FILE_MAGIC          0x31504948u     /* "HIP1" */
#define FILE_SIZE           12u             /* magic, MIDI, synths, 2 zero bytes, 4-byte checksum */

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
    int n = ((snprintf_fn)FW_SNPRINTF)(path, size, "%s/%s/interpolate.bin",
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
    ok = ok && get32(buf) == FILE_MAGIC && get32(buf + 8) == data_sum(buf + 4) && buf[4] <= 1 && buf[5] <= 1;
    if (!ok)
        return 0;
    IP->value = IP->saved = buf[4];
    IP->synth = IP->saved_synth = buf[5];   /* 0 in files from before the synth setting */
    IP->saved_valid = 1;
    return 1;
}

static void settings_save(void)
{
    char path[256];
    void *fs = *(void **)FW_FM;
    if (!fs || !settings_path(path, sizeof path))
        return;
    void **vt = *(void ***)fs;
    uint8_t buf[FILE_SIZE] = {0};
    uint8_t value = IP->value, synth = IP->synth;
    put32(buf, FILE_MAGIC);
    buf[4] = value;
    buf[5] = synth;
    put32(buf + 8, data_sum(buf + 4));
    struct fs_open_result res = {0, 0, {0, 0, 0}};
    ((fs_remove_fn)vt[FS_REMOVE / 4])(fs, path);
    ((fs_open_fn)vt[FS_OPEN / 4])(&res, fs, path, FS_MODE_WRITE, 0);
    if (!res.ok)
        return;
    int ok = ((fs_write_fn)vt[FS_WRITE / 4])(fs, buf, FILE_SIZE, res.handle) == (int)FILE_SIZE;
    ((fs_close_fn)vt[FS_CLOSE / 4])(fs, res.handle, 1);
    if (ok) {
        IP->saved = value;
        IP->saved_synth = synth;
        IP->saved_valid = 1;
    }
}

/* Called from the Pattern and Config screens' update methods (where the stock code
 * does its own file I/O). At most one small file read or write per call. */
static void settings_tick(void)
{
    ip_init_if_needed();
    if (!*(void **)FW_FM)
        return;
    uint32_t session = project_session();
    int unsaved = project_unsaved();
    if (session != IP->session) {
        int save_as = session == IP->save_as;   /* the same project under its new name */
        IP->save_as = 0;
        IP->session = session;
        IP->saved_valid = 0;                /* after a Save As: written to the new folder */
        IP->wait = SAVE_DELAY;
        IP->load = (uint8_t)(!unsaved && !save_as);
        if (!save_as)
            IP->value = IP->synth = 0;      /* another project: its file, once read, decides */
    }
    if (!file_manager_idle())
        return;
    if (IP->load) {
        IP->load = 0;
        settings_load();                    /* none or a bad one: Off */
        IP->saved = IP->value;
        IP->saved_synth = IP->synth;
        IP->saved_valid = 1;                /* nothing to write until the value changes */
        return;
    }
    if (unsaved || (IP->saved_valid && IP->value == IP->saved && IP->synth == IP->saved_synth)) {
        IP->wait = SAVE_DELAY;
        return;
    }
    if (IP->wait) {
        IP->wait--;
        return;
    }
    IP->wait = SAVE_DELAY;
    settings_save();
}

/* Pattern screen vtable slot 0x600403dc (update method 0x600e1f99; chained) */
void mt_ip_pattern_update(void *screen, void *a1, void *a2, void *a3)
{
    settings_tick();
    ((update_fn)mt_ip_next_pattern_update)(screen, a1, a2, a3);
}

/* Config screen vtable slot 0x6003b5f8 (update method 0x600c6475; chained) */
void mt_ip_config_update(void *screen, void *a1, void *a2, void *a3)
{
#ifdef IP_DIAG
    if (IP->magic == IP_MAGIC)
        diag_format();
#endif
    settings_tick();
    ((update_fn)mt_ip_next_config_update)(screen, a1, a2, a3);
}

/* Project screen vtable slot 0x6004193c (update method 0x600ead59; chained): New
 * Project, Open and Save As happen there, so a new project is seen before its first
 * save even when no other hooked screen showed in between. */
uint32_t mt_ip_project_update(void *screen, void *a1, void *a2, void *a3)
{
    settings_tick();
    return ((project_update_fn)mt_ip_next_project_update)(screen, a1, a2, a3);
}

/* 0x60114eaa in the save request 0x60114e34: "bl strncpy(fm + 0x340, name, 0x21)",
 * run only for Save As, after the new path is copied. The next tick sees the new
 * session and keeps the settings for it. */
char *mt_ip_save_name(char *dst, const char *src, uint32_t n)
{
    char *r = ((strncpy_fn)mt_ip_next_save_name)(dst, src, n);
    ip_init_if_needed();
    IP->save_as = project_session();
    return r;
}

/* ---- version text: Config shows 1.2.0-<INTERPOLATE_VERSION> --------------------- */
const char mt_version_fmt[] = "%d.%d.%d-" INTERPOLATE_VERSION;
