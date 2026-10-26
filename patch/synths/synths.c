/*
 * synths: WAVE, a wavetable synth, and PHASE, a phase distortion synth, each of which can
 * stand in for one of the five synth models, chosen per project (Polyend Tracker+
 * firmware 1.2.0).
 *
 * Project Settings gets a "Synth Engines" folder with two items, "WAVE replaces" and
 * "PHASE replaces": Off (the default), ACD, FAT, VAP, WTFM or PERC. The Instrument
 * screen's Model list then shows WAVE (or PHASE) in that model's place, the instrument
 * title reads "Synth 1 (WAVE)", and the synth pages show the engine's own controls. The
 * two can replace two different models; set to the same one, the item changed last
 * wins and the other goes back to Off. Each named project keeps its choices in
 * <project folder>/synths.bin; a project without the file, and every project in stock
 * firmware, plays the stock models.
 *
 * Everything below is described for WAVE; PHASE works the same way with its own model
 * id (7), builder, engine (ph_engine.h) and parameter map. Its Edit Patch tiles get CZ
 * names (DCO, DCW, DCA, DCW Env, DCO Env) from one more hook, after the stock function
 * that names the tiles from the pages' names (docs/PD_SYNTH_PLAN.md section 4.1).
 *
 * How (docs/FINDINGS.md section 23 has the map):
 *
 * - WAVE is a model of its own, id 3 (free in stock firmware). A hook on the model
 *   registry's builder inserts its descriptor: the name "WAVE", a parameter builder and
 *   VAP's voice factory. Its patches carry model id 3, so stock firmware rejects a WAVE
 *   slot's saved patch and plays the stock model's Init patch instead.
 * - WAVE's builder runs VAP's builder, then rewrites the patch it made: parameter
 *   names, ranges, value displays (Wave 1 "Super Saw", Type "LP Ladder 24"), the screen
 *   layout (groups), the Macro editor's pages, the Macros, the Init values and the model
 *   id. The 60 entries stay where VAP has them, so the engine reads VAP's indices. Six
 *   entries of WAVE's own follow (60, 61: the LFOs' Polarity; 62-65: Effect, Rate, Depth,
 *   Mix). The firmware sizes a patch's per-parameter arrays from the count the builder
 *   leaves, and VAP's builder resizes the vector to its 60 first, so every build of a
 *   WAVE patch has 66. A WAVE patch has 2 output channels (as PERC's), for the effect.
 * - Patch files hold the values by position, and the loader sets as many as the file
 *   has. So before a WAVE patch loads, a hook on the patch storage's load sets WAVE's own
 *   six to their defaults (Bipolar, Effect None): a file saved before they existed loads
 *   as it played.
 * - The swap is the project's model index table (DTCM 0x200006b4: index 0-4 -> model
 *   id): the replaced index maps to 3. Records keep stock indices. At project load the
 *   patch reads synths.bin before the stock code applies the slot records; a change in
 *   the menu re-sets the affected slots' models at the next Config screen update.
 * - The Model list draws model logos by image id. With a swap the patch hands it its
 *   own id table, with the replaced position marked, and draws "WAVE" as text there. The
 *   Edit Patch screen's logo label gets no logo and the text "WAVE" for a WAVE synth.
 * - A WAVE slot's voices are VAP voice objects. Five of the VAP voice vtable's words
 *   are hooked (deleting destructor +0x04, MIDI +0x08, set parameter +0x0c, render
 *   +0x10, +0x14 after the slot's voices have rendered, "still sounding" +0x18), each
 *   through a chain word. For a voice whose patch is WAVE's the engine (wt_engine.h)
 *   renders and answers "sounding", the effect (wt_fx.h) runs on the slot's output after
 *   voice 0's render, and set_param
 *   fills the slot's parameter copy without reaching VAP (WAVE's values don't fit
 *   VAP's ranges). MIDI messages always reach VAP too, so its note bookkeeping, which
 *   the slot's voice allocation reads, stays right. Every other voice is stock.
 *
 * The voice hooks are naked trampolines: they save r0-r3, call an observer with a
 * pointer to the saved registers and the caller's stack arguments, and then either
 * return the observer's result or jump on through the chain word with every register
 * and stack word as the caller left them (the render call has five stack arguments).
 *
 * Start-up. Until the Pattern screen's update method first runs, a WAVE voice renders
 * silence: heavy DSP while the unit boots hung it (masterfx, docs/FINDINGS.md section 19).
 *
 * Shared hooks. The Project Settings folder, the menu constructor's last item and the
 * Pattern and Config screens' update methods are hooked through chain words
 * (mt_syn_next_*), as every other hook here is, so other patches can hook them too.
 */
#include <stddef.h>
#include <stdint.h>

#ifndef SYNTHS_VERSION
#define SYNTHS_VERSION "sy9"
#endif

/* Chain words, filled in by tools/patch_ptf.py ("next" in patch.json). volatile: the
 * compiler must read them, not assume the 0 they are linked with. */
#define CHAIN_WORD(name) const volatile uint32_t name __attribute__((used)) = 0
CHAIN_WORD(mt_syn_next_delete);             /* VAP voice +0x04: 0x6014b725 */
CHAIN_WORD(mt_syn_next_midi);               /* VAP voice +0x08: 0x6014a9a9 */
CHAIN_WORD(mt_syn_next_set_param);          /* VAP voice +0x0c: 0x6014a8f1 */
CHAIN_WORD(mt_syn_next_render);             /* VAP voice +0x10: 0x6014a8b1 */
CHAIN_WORD(mt_syn_next_post);               /* VAP voice +0x14: 0x6014a811 (nothing) */
CHAIN_WORD(mt_syn_next_sounding);           /* VAP voice +0x18: 0x6014a81d */
CHAIN_WORD(mt_syn_next_folder);             /* Project Settings: cfg_menu_folder 0x60118395 */
CHAIN_WORD(mt_syn_next_last_item);          /* last item: cfg_menu_item 0x601183e9 */
CHAIN_WORD(mt_syn_next_pattern_update);     /* Pattern screen update 0x600e1f99 */
CHAIN_WORD(mt_syn_next_config_update);      /* Config screen update 0x600c6475 */
CHAIN_WORD(mt_syn_next_registry);           /* the model registry's builder 0x60140151 */
CHAIN_WORD(mt_syn_next_records);            /* synth_slots_apply_project_q 0x600b2081 */
CHAIN_WORD(mt_syn_next_list_data);          /* disp_ctrl_set_data 0x60101df9 (Model list) */
CHAIN_WORD(mt_syn_next_list_images);        /* the list control's image items 0x6010645d */
CHAIN_WORD(mt_syn_next_patch_label);        /* disp_ctrl_set_text 0x60101d5d (Edit Patch's logo label) */
CHAIN_WORD(mt_syn_next_patch_load);         /* patch storage +0x0c, load into a patch: 0x60124f39 */
CHAIN_WORD(mt_syn_next_tiles);              /* the Edit Patch tiles' labels 0x6011d629 */
CHAIN_WORD(mt_syn_next_save_name);          /* strncpy 0x601673d7 (Save As: the new name) */

/* ---- firmware 1.2.0 addresses (function pointers carry the Thumb bit) ----------- */
#define FW_SYNTHS           0x200170E8u     /* the synth system S (app + 8) */
#define SYN_SLOT_STRIDE     0x30u           /* S + 0x30 * i: slot i's model id */
#define SYN_SLOT_OBJ        0x04u           /* ... + 4: the slot object */
#define SYN_SLOT_PATCH      0x0Cu           /* ... + 0xc: the slot's patch object */
#define SYN_REGISTRY        0x9Cu           /* S + 0x9c: the model registry wrapper (0 until the synths exist) */
#define SLOT_NVOICES        0x174u          /* slot object: number of voices */
#define SLOT_VOICE(r)       (0x14u + 0x2Cu * (r))   /* slot object: voice record r, word 0 */
#define VOICE_PATCH         0x04u           /* voice + 4: its slot's patch object */
#define PATCH_MODEL         0x78u           /* patch + 0x54 (container) + 0x24: model id */
#define PATCH_VALUES        0x0Cu           /* patch: the current values (vector<float>) */
#define PATCH_SET_VALUE     0x10u           /* patch vtable: set_value(patch, index, float *, source) */
#define FW_PATCH_VTABLE     0x6006307Cu     /* every model's patch class */
#define MODEL_WAVE          3u              /* WAVE's model id (no stock model has it) */
#define MODEL_PHASE         7u              /* PHASE's (neither) */
#define MODELS              5u              /* the Model list: ACD, FAT, VAP, WTFM, PERC */
#define SLOTS               3u
#define FW_MODEL_TABLE      ((volatile uint32_t *)0x200006B4u)  /* model index -> model id */
#define FW_RECORD_MODEL(i)  (*(volatile uint32_t *)(0x20204D08u + 0x40u * (i)))  /* project record +8 */
#define FW_SET_MODEL        0x600B1441u     /* synth_slot_set_model(S, slot, id) */
#define FW_IRQ_DISABLE      0x600303D5u     /* audio_irq_disable: NVIC ICER0 bit 0 */
#define FW_IRQ_ENABLE       0x600303C9u     /* audio_irq_enable: NVIC ISER0 bit 0 */
#define NVIC_ISER0          ((volatile uint32_t *)0xE000E100u)
#define FW_FM               0x2001E488u     /* file manager object; *FW_FM = FS object */
#define FW_PROJECT_PATH     ((const uint8_t *)(FW_FM + 0x241u))
#define FW_PROJECT_NAME     ((const uint8_t *)(FW_FM + 0x340u))
#define FM_STATE            0x13Du          /* file manager state; 0 or 0x0c when idle */
#define FM_SUBSTATE         0x13Fu
#define PATH_MAX_LEN        254u
#define NAME_MAX_LEN        32u
#define FW_PROJECT_SETTINGS 0x20028714u     /* Config menu folder "Project Settings" */
#define FW_MENU_FOLDER      0x60118395u     /* (node, parent, label, capacity) */
#define FW_MENU_ITEM        0x601183E9u     /* (node, parent, label, help, type, descriptor) */
#define FW_MALLOC           0x600A3589u     /* (size) */
#define FW_DELETE           0x6016A085u     /* sized operator delete (ptr, size) */
#define FW_SNPRINTF         0x6016712Du
#define FW_REGISTRY_INSERT  0x6012FD79u     /* (registry, id, descriptor **): takes it, or leaves it */
#define FW_VAP_BUILDER      0x6014DEA1u     /* VAP's parameter builder (patch, container) */
#define FW_PARAMS_APPEND    0x60148029u     /* vector<parameter entry>::_M_default_append(v, n) */
#define FW_FN_MANAGER       0x6014495Du     /* std::function manager for a function pointer */
#define FW_FN_INVOKER       0x60144955u     /* ... and its invoker: (*functor)(a, b) */
#define FW_VAP_MACROS       0x6014D161u     /* VAP's Macro defaults (functor, &Macro vector) */
#define FW_VAP_NEW_MANAGER  0x6014A7C5u     /* VAP's voice factory: manager */
#define FW_VAP_NEW_INVOKER  0x6014A959u     /* ... and invoker */
#define FW_EVE_TEXT         0x6011FA89u     /* eve_cmd_text_q(x, y, font, options, text) */
#define EVE_OPT_CENTER      0x600u

typedef void (*void_fn)(void);
typedef void (*screen_update_fn)(void *screen, void *a1, void *a2, void *a3);
typedef void *(*folder_fn)(void *node, void *parent, const char *label, uint32_t capacity);
typedef void *(*item_fn)(void *node, void *parent, const char *label, const char *help,
                         uint32_t type, const void *descriptor);
typedef void *(*malloc_fn)(uint32_t size);
typedef void (*delete_fn)(void *p, uint32_t size);
typedef int (*snprintf_fn)(char *buf, uint32_t size, const char *fmt, ...);
typedef void (*set_model_fn)(void *s, uint32_t slot, uint32_t id);
typedef char *(*strncpy_fn)(char *dst, const char *src, uint32_t n);

/* ---- state (.mt_ram: neither loaded nor zeroed; the patcher places it) ----------- */

#define REPLACE_NONE        0xFFu           /* "WAVE replaces" Off */
#define SY_MAGIC            0x33594E53u     /* "SNY3": the settings fields are valid (SNY2: sy7's, without PHASE's) */
#define SY_READY            0x59444552u     /* "REDY": booted, the engine may run */
#define SAVE_DELAY          25u             /* screen updates between a change and the save */
#define MAX_VOICES          8u              /* the stock cap for all three slots together */
#define LIST_TEXT_ID        0x7Fu           /* an image id the list skips (ids below 0x24 draw): WAVE's row */
#define LIST_TEXT_PHASE     0x7Eu           /* ... PHASE's row */

#include "wt_engine.h"
#include "ph_engine.h"

/* The engines: an engine voice's tag, and what a voice's patch is */
#define ENGINE_WAVE         0u
#define ENGINE_PHASE        1u
#define ENGINE_NONE         0xFFu           /* a stock model's voice */
#define CUSTOM_PARAMS       66u             /* WAVE's and PHASE's patches: VAP's 60 and the same own six */
_Static_assert(WAVE_PARAMS == CUSTOM_PARAMS && PHASE_PARAMS == CUSTOM_PARAMS, "the own six after VAP's 60");

/* An engine voice of either engine; both begin with owner, slot, engine */
union sy_voice {
    struct { uint32_t owner; uint16_t slot; uint8_t engine; uint8_t pad0; } h;
    struct wt_voice wt;
    struct ph_voice ph;
};
_Static_assert(sizeof(union sy_voice) == sizeof(struct wt_voice), "PHASE's voice fits WAVE's");
_Static_assert(offsetof(struct ph_voice, engine) == offsetof(struct wt_voice, engine), "the voices' heads");

struct syn_state {
    uint32_t magic;
    uint32_t session;                       /* project name + path the choice belongs to */
    uint8_t menu;                           /* WAVE's choice: 0 Off, 1 + the model index */
    uint8_t saved;                          /* what synths.bin holds */
    uint8_t saved_valid;
    uint8_t unnamed;                        /* the session is a project without a name */
    uint8_t wait;                           /* updates left before saving */
    uint8_t applied;                        /* the model index the table maps to WAVE, or REPLACE_NONE */
    uint8_t fx_ready;                       /* the late RAM block is set up (reset at every start) */
    uint8_t menu_pos;                       /* the menu edits this: the position in WAVE's list */
    /* the audio side (reset at every start) */
    uint32_t ready;                         /* SY_READY once the unit has booted */
    uint32_t switches;                      /* slots the patch moved to another model (for the tests) */
    uint32_t loads;                         /* project loads seen (for the tests) */
    int32_t model_ids[MODELS];              /* the Model list's image ids while WAVE or PHASE replaces one */
    float param[SLOTS][WT_PARAMS];          /* each slot's copy of the parameters its engine uses */
    union sy_voice voice[MAX_VOICES];       /* the engines' voices; .h.owner 0 = free */
    /* PHASE's, as menu, saved and applied; last, so the rest is where sy7 had it (WAVE's
     * voices start their oscillators from their addresses) */
    uint8_t ph_menu;
    uint8_t ph_saved;
    uint8_t ph_applied;
    uint8_t ph_menu_pos;
};
/* the all bundle has 2,064 bytes for it (dtcm_above_heap, docs/PD_SYNTH_PLAN.md section 1) */
_Static_assert(sizeof(struct syn_state) <= 2064u, "synths' startup RAM");
static struct syn_state syn_state_ram __attribute__((section(".mt_ram")));
#define SY (&syn_state_ram)

/* The effects' state: the late RAM block (tools/patch_ptf.py "late_ram"). The word holds
 * the block's size as linked and its address once patched; it may be SDRAM, so it is only
 * touched once the unit has booted (SY->ready). */
#include "wt_fx.h"
struct fx_ram { struct wt_fx fx[SLOTS]; };
const volatile uint32_t mt_syn_late_ram __attribute__((used)) = sizeof(struct fx_ram);
#define FXR ((struct fx_ram *)mt_syn_late_ram)

static const uint32_t stock_model_ids[MODELS] = {1u, 2u, 4u, 5u, 6u};   /* ACD FAT VAP WTFM PERC */
static const int32_t stock_image_ids[MODELS] = {9, 10, 11, 12, 24};     /* their logos (0x60061488) */

static void sy_init_if_needed(void)         /* RAM outside .bss holds garbage at power-on */
{
    if (SY->magic == SY_MAGIC)
        return;
    SY->menu = SY->ph_menu = 0;
    SY->session = 0;
    SY->saved_valid = 0;
    SY->unnamed = 0;
    SY->wait = 0;
    SY->applied = SY->ph_applied = REPLACE_NONE;    /* as at a start: the stock table */
    SY->magic = SY_MAGIC;
}

/* The audio side back to stock: at every start (the menu constructor, a static
 * constructor, so before the synths exist; the model table is fresh .data then) */
static void sy_audio_reset(void)
{
    SY->ready = 0;
    SY->fx_ready = 0;
    SY->applied = SY->ph_applied = REPLACE_NONE;
    for (uint32_t k = 0; k < MAX_VOICES; k++)
        SY->voice[k].h.owner = 0;
    SY->switches = 0;
    SY->loads = 0;
}

/* ---- slots and voices ------------------------------------------------------------ */

static uint32_t slot_word(uint32_t i, uint32_t off)
{
    return *(volatile uint32_t *)(FW_SYNTHS + SYN_SLOT_STRIDE * i + off);
}

/* The slot (0..2) a voice belongs to, from voice + 4 (its patch object), or -1 */
static int slot_of(const uint8_t *voice)
{
    uint32_t patch = *(const volatile uint32_t *)(voice + VOICE_PATCH);
    for (uint32_t i = 0; i < SLOTS; i++)
        if (patch && slot_word(i, SYN_SLOT_PATCH) == patch)
            return (int)i;
    return -1;
}

/* The engine of a model id: ENGINE_WAVE, ENGINE_PHASE or ENGINE_NONE */
static uint32_t engine_of_model(uint32_t id)
{
    return id == MODEL_WAVE ? ENGINE_WAVE : id == MODEL_PHASE ? ENGINE_PHASE : ENGINE_NONE;
}

/* The engine of the voice's patch */
static uint32_t engine_of(const uint8_t *voice)
{
    uint32_t patch = *(const volatile uint32_t *)(voice + VOICE_PATCH);
    return patch ? engine_of_model(*(const volatile uint32_t *)(patch + PATCH_MODEL)) : ENGINE_NONE;
}

static union sy_voice *entry_of(const void *voice)
{
    for (uint32_t k = 0; k < MAX_VOICES; k++)
        if (SY->voice[k].h.owner == (uint32_t)voice)
            return &SY->voice[k];
    return 0;
}

static uint32_t in_handler(void)            /* inside an interrupt (the audio one) */
{
    uint32_t ipsr;
    __asm volatile("mrs %0, ipsr" : "=r"(ipsr));
    return ipsr & 0x1FFu;
}

/* The audio interrupt off, if it is on and we aren't inside it; returns what to restore.
 * (Callers such as synth_slot_set_model already turn it off: it must stay off.) */
static uint32_t audio_irq_save(void)
{
    if (in_handler() || !(*NVIC_ISER0 & 1u))
        return 0;
    ((void_fn)FW_IRQ_DISABLE)();
    return 1;
}

static void audio_irq_restore(uint32_t on)
{
    if (on)
        ((void_fn)FW_IRQ_ENABLE)();
}

/* An engine voice for a voice of a WAVE or PHASE slot, or 0 when all 8 are taken (that
 * voice then stays silent until one is free). Not interrupted by the audio interrupt. */
static union sy_voice *entry_new(const void *voice, uint32_t slot, uint32_t engine)
{
    uint32_t irq = audio_irq_save();
    union sy_voice *e = entry_of(voice);
    for (uint32_t k = 0; !e && k < MAX_VOICES; k++)
        if (!SY->voice[k].h.owner) {
            e = &SY->voice[k];
            if (engine == ENGINE_PHASE)
                ph_voice_init(&e->ph, slot);
            else
                wt_voice_init(&e->wt, slot);
            e->h.engine = (uint8_t)engine;
            e->h.pad0 = 0;
            e->h.owner = (uint32_t)voice;
        }
    audio_irq_restore(irq);
    return e;
}

/* The engine runs this voice: booted, a WAVE or PHASE voice of a slot, with an engine
 * voice of its engine. */
static union sy_voice *engine_voice(const uint8_t *voice, int *slot)
{
    uint32_t engine = engine_of(voice);
    if (SY->ready != SY_READY || engine == ENGINE_NONE)
        return 0;
    int i = slot_of(voice);
    *slot = i;
    if (i < 0)
        return 0;
    union sy_voice *e = entry_of(voice);
    return e && e->h.slot == (uint32_t)i && e->h.engine == engine ? e : 0;
}

/* Entries whose voice no slot holds any more (a path that bypassed the delete hook), or
 * whose voice now plays another engine, are freed; then a free entry goes to a WAVE or
 * PHASE voice that has none (one made while all 8 were taken, silent so far). */
static void sweep(void)
{
    for (uint32_t k = 0; k < MAX_VOICES; k++) {
        uint32_t v = SY->voice[k].h.owner;
        if (!v)
            continue;
        int held = 0;
        for (uint32_t i = 0; i < SLOTS && !held; i++) {
            const uint8_t *slot = (const uint8_t *)slot_word(i, SYN_SLOT_OBJ);
            uint32_t n = slot ? *(const volatile uint32_t *)(slot + SLOT_NVOICES) : 0;
            for (uint32_t r = 0; r < n && r < MAX_VOICES; r++)
                if (*(const volatile uint32_t *)(slot + SLOT_VOICE(r)) == v)
                    held = 1;
        }
        if (!held || engine_of((const uint8_t *)v) != SY->voice[k].h.engine)
            SY->voice[k].h.owner = 0;
    }
    for (uint32_t i = 0; i < SLOTS; i++) {
        uint8_t *slot = (uint8_t *)slot_word(i, SYN_SLOT_OBJ);
        if (!slot)
            continue;
        uint32_t n = *(volatile uint32_t *)(slot + SLOT_NVOICES);
        for (uint32_t r = 0; r < n && r < MAX_VOICES; r++) {
            const uint8_t *v = (const uint8_t *)*(volatile uint32_t *)(slot + SLOT_VOICE(r));
            uint32_t engine = v ? engine_of(v) : ENGINE_NONE;
            if (engine != ENGINE_NONE && slot_of(v) == (int)i && !entry_of(v))
                entry_new(v, i, engine);
        }
    }
}

/* ---- the swap ---------------------------------------------------------------------- */

/* The model index table (and the Model list's ids) with `wave` mapped to WAVE and
 * `phase` to PHASE (REPLACE_NONE: neither; PHASE gives way if both name one index) */
static void swap_table(uint32_t wave, uint32_t phase)
{
    if (phase == wave)
        phase = REPLACE_NONE;
    for (uint32_t k = 0; k < MODELS; k++) {
        FW_MODEL_TABLE[k] = stock_model_ids[k];
        SY->model_ids[k] = stock_image_ids[k];
    }
    if (wave < MODELS) {
        FW_MODEL_TABLE[wave] = MODEL_WAVE;
        SY->model_ids[wave] = LIST_TEXT_ID;
    }
    if (phase < MODELS) {
        FW_MODEL_TABLE[phase] = MODEL_PHASE;
        SY->model_ids[phase] = LIST_TEXT_PHASE;
    }
    SY->applied = (uint8_t)(wave < MODELS ? wave : REPLACE_NONE);
    SY->ph_applied = (uint8_t)(phase < MODELS ? phase : REPLACE_NONE);
}

static uint32_t replace_of(uint32_t menu)
{
    return menu >= 1 && menu <= MODELS ? menu - 1u : REPLACE_NONE;
}

/* The two menu items set to one model: the one changed since the choices last applied
 * wins, and the other goes back to Off (WAVE wins when both or neither changed, as a
 * synths.bin with one model in both loads) */
static void menu_conflict(void)
{
    uint32_t w = replace_of(SY->menu), ph = replace_of(SY->ph_menu);
    if (w == REPLACE_NONE || w != ph)
        return;
    if (ph != SY->ph_applied && w == SY->applied)
        SY->menu = 0;
    else
        SY->ph_menu = 0;
}

/* The menu's choices to the table and to the slots whose model they change (screen
 * updates only, as the Model list's own changes are; once the synth system exists) */
static void apply_menu(void)
{
    menu_conflict();
    uint32_t w = replace_of(SY->menu), ph = replace_of(SY->ph_menu);
    if ((w == SY->applied && ph == SY->ph_applied) || !slot_word(0, SYN_REGISTRY))
        return;
    swap_table(w, ph);
    for (uint32_t i = 0; i < SLOTS; i++) {
        uint32_t index = FW_RECORD_MODEL(i);
        if (index >= MODELS || !slot_word(i, SYN_SLOT_OBJ))
            continue;
        uint32_t id = FW_MODEL_TABLE[index];
        if (slot_word(i, 0) != id) {
            ((set_model_fn)FW_SET_MODEL)((void *)FW_SYNTHS, i, id);
            SY->switches++;
        }
    }
}

/* ---- the voice hooks --------------------------------------------------------------- */

/* A trampoline: saves r0-r3 (with r4 and lr, 24 bytes, so the stack stays 8-aligned),
 * calls observer(frame), frame = {r0, r1, r2, r3, r4, lr, the caller's stack arguments},
 * then returns the observer's result if it is not negative, or else jumps on through
 * the chain word with every register and stack word as the caller left them. */
#define TRAMPOLINE(name, observer, chain)                                               \
    __attribute__((naked, used)) void name(void)                                        \
    {                                                                                   \
        __asm volatile("push {r0, r1, r2, r3, r4, lr}\n\t"                              \
                       "mov r0, sp\n\t"                                                 \
                       "bl " #observer "\n\t"                                           \
                       "cmp r0, #0\n\t"                                                 \
                       "blt 1f\n\t"                                                     \
                       "add sp, sp, #4\n\t"                                             \
                       "pop {r1, r2, r3, r4, pc}\n"                                     \
                       "1:\n\t"                                                         \
                       "pop {r0, r1, r2, r3, r4, lr}\n\t"                               \
                       "ldr r12, 2f\n\t"                                                \
                       "ldr r12, [r12]\n\t"                                             \
                       "bx r12\n\t"                                                     \
                       ".align 2\n"                                                     \
                       "2: .word " #chain "\n");                                        \
    }

#define FRAME_STACK         6u              /* frame index of the caller's first stack word */
#define CHAIN               (-1)

/* +0x04 deleting destructor (voice): free the engine voice, then the stock one */
__attribute__((used)) int sy_obs_delete(const uint32_t *f)
{
    union sy_voice *e = entry_of((const void *)f[0]);
    if (e)
        e->h.owner = 0;
    return CHAIN;
}

/* +0x08 midi (voice, ctx, msg {type, 0, data1, data2}): the engine's notes, then VAP's */
__attribute__((used)) int sy_obs_midi(const uint32_t *f)
{
    int slot;
    union sy_voice *e = engine_voice((const uint8_t *)f[0], &slot);
    if (e && e->h.engine == ENGINE_PHASE)
        ph_voice_midi(&e->ph, (const uint8_t *)f[2], SY->param[slot]);
    else if (e)
        wt_voice_midi(&e->wt, (const uint8_t *)f[2], SY->param[slot]);
    return CHAIN;
}

/* +0x0c set_param (voice, index, float *value): a WAVE or PHASE voice's go into its slot's
 * copy (each engine's map) and stop there; every other voice's go to VAP, except an index
 * past VAP's 60 (VAP's handler table has 60 entries) */
__attribute__((used)) int sy_obs_set_param(const uint32_t *f)
{
    const uint8_t *voice = (const uint8_t *)f[0];
    uint32_t index = f[1];
    uint32_t engine = engine_of(voice);
    if (engine == ENGINE_NONE)
        return index < VAP_PARAMS ? CHAIN : 0;
    int i = slot_of(voice);
    if (i < 0)
        return 0;
    float v = *(const volatile float *)f[2];
    if (index >= FX_FIRST && index < FX_FIRST + FX_PARAMS) {    /* the effect's: once it is set up */
        if (SY->fx_ready)
            FXR->fx[i].p[index - FX_FIRST] = v;
        return 0;
    }
    const uint8_t *map = engine == ENGINE_PHASE ? ph_param_slot : wt_param_slot;
    if (index < CUSTOM_PARAMS && map[index] != WT_UNUSED)
        SY->param[i][map[index]] = v;
    union sy_voice *e = entry_of(voice);
    if (!e)
        e = entry_new(voice, (uint32_t)i, engine);
    if (e && index == VAP_VOLUME) {
        if (e->h.engine == ENGINE_PHASE)
            e->ph.volume = v;
        else
            e->wt.volume = v;
    }
    return 0;
}

/* +0x10 render (voice, ctx, 0, 0, [sp] 0, [sp+4] L, [sp+8] R, [sp+0xc] 2, [sp+0x10] ...):
 * a WAVE or PHASE voice: the engine overwrites L (mono, as VAP), or 128 zeros before the
 * unit has booted or while it has no engine voice; every other voice: VAP renders */
__attribute__((used)) int sy_obs_render(const uint32_t *f)
{
    const uint8_t *voice = (const uint8_t *)f[0];
    if (engine_of(voice) == ENGINE_NONE)
        return CHAIN;
    float *out = (float *)f[FRAME_STACK + 1];
    int slot;
    union sy_voice *e = engine_voice(voice, &slot);
    const float *ctx = (const float *)f[1];     /* the render context: ctx[0] is the tempo */
    float tempo = ctx ? ctx[0] : 120.0f;
    if (e && e->h.engine == ENGINE_PHASE)
        ph_voice_render(&e->ph, SY->param[slot], out, tempo);
    else if (e && WT_ENGINE_ON)
        wt_voice_render(&e->wt, SY->param[slot], out, tempo);
    else
        for (uint32_t n = 0; n < WT_BLOCK; n++)
            out[n] = 0.0f;
    return 0;
}

/* +0x18 sounding (voice): a WAVE or PHASE voice: the engine's amp envelope is not idle */
__attribute__((used)) int sy_obs_sounding(const uint32_t *f)
{
    const uint8_t *voice = (const uint8_t *)f[0];
    if (engine_of(voice) == ENGINE_NONE)
        return CHAIN;
    int slot;
    union sy_voice *e = engine_voice(voice, &slot);
    if (!e)
        return 0;
    return e->h.engine == ENGINE_PHASE ? ph_voice_sounding(&e->ph) : wt_voice_sounding(&e->wt);
}

static float *custom_values(uint8_t *patch);

/* A slot's effect state, once the unit has booted. The first call after a start sets
 * the block up: cleared, with each slot's effect values from its patch (set_param only
 * keeps them from then on). Called from the audio interrupt only. */
static struct wt_fx *fx_slot(uint32_t slot)
{
    if (SY->ready != SY_READY)
        return 0;
    if (!SY->fx_ready) {
        uint32_t *w = (uint32_t *)FXR;
        for (uint32_t k = 0; k < sizeof(struct fx_ram) / 4u; k++)
            w[k] = 0;
        for (uint32_t i = 0; i < SLOTS; i++) {
            const float *v = custom_values((uint8_t *)slot_word(i, SYN_SLOT_PATCH));
            const float *src = v ? v + FX_FIRST : fx_defaults;
            for (uint32_t k = 0; k < FX_PARAMS; k++)
                FXR->fx[i].p[k] = src[k];
        }
        SY->fx_ready = 1;
    }
    return &FXR->fx[slot];
}

/* +0x14 (voice, ctx, L, R), voice 0's, once per block after the slot's voices have
 * rendered into L and R (the slot's output): for a WAVE or PHASE slot, the effect, which
 * writes both sides (its patch has two channels); before boot, R = L. Others: VAP's
 * (nothing). */
__attribute__((used)) int sy_obs_post(const uint32_t *f)
{
    const uint8_t *voice = (const uint8_t *)f[0];
    if (engine_of(voice) == ENGINE_NONE)
        return CHAIN;
    float *L = (float *)f[2], *R = (float *)f[3];
    int slot = slot_of(voice);
    struct wt_fx *s = slot >= 0 ? fx_slot((uint32_t)slot) : 0;
    if (s) {
        if (s->voice != (uint32_t)voice) {  /* another patch or voice set: start over */
            s->voice = (uint32_t)voice;
            s->running = 0;
        }
        wt_fx_run(s, L, R);
    } else
        for (uint32_t n = 0; n < WT_BLOCK; n++)
            R[n] = L[n];
    return 0;
}

TRAMPOLINE(mt_syn_voice_delete, sy_obs_delete, mt_syn_next_delete)
TRAMPOLINE(mt_syn_voice_midi, sy_obs_midi, mt_syn_next_midi)
TRAMPOLINE(mt_syn_voice_set_param, sy_obs_set_param, mt_syn_next_set_param)
TRAMPOLINE(mt_syn_voice_render, sy_obs_render, mt_syn_next_render)
TRAMPOLINE(mt_syn_voice_post, sy_obs_post, mt_syn_next_post)
TRAMPOLINE(mt_syn_voice_sounding, sy_obs_sounding, mt_syn_next_sounding)

/* ---- the firmware's C++ objects ------------------------------------------------------ */

/* libstdc++'s std::string (GCC 10): pointer, length, then a 16-byte local buffer (or the
 * capacity when the text is on the heap) */
struct fw_string { char *ptr; uint32_t len; union { uint32_t cap; char buf[16]; } u; };
struct fw_vector { uint8_t *begin, *end, *cap; };

static uint32_t cstr_len(const char *s)
{
    uint32_t n = 0;
    while (s[n])
        n++;
    return n;
}

/* A string set to t (at most 15 characters: kept in the local buffer) */
static void str_init(struct fw_string *s, const char *t)
{
    uint32_t n = cstr_len(t);
    if (n > 15)
        n = 15;
    s->ptr = s->u.buf;
    for (uint32_t k = 0; k < n; k++)
        s->u.buf[k] = t[k];
    s->u.buf[n] = 0;
    s->len = n;
}

static void str_free(struct fw_string *s)
{
    if (s->ptr != s->u.buf)
        ((delete_fn)FW_DELETE)(s->ptr, s->u.cap + 1u);
    s->ptr = s->u.buf;
}

static void str_set(struct fw_string *s, const char *t)
{
    str_free(s);
    str_init(s, t);
}

static int str_eq(const struct fw_string *s, const char *t)
{
    uint32_t n = cstr_len(t);
    if (s->len != n)
        return 0;
    for (uint32_t k = 0; k < n; k++)
        if (s->ptr[k] != t[k])
            return 0;
    return 1;
}

static uint32_t vec_count(const struct fw_vector *v, uint32_t size)
{
    return (uint32_t)(v->end - v->begin) / size;
}

/* ---- WAVE and PHASE: the descriptors and their builders -------------------------------------------- */

/* A parameter entry (0x38 bytes), a screen-layout group and its sub-screens, a Macro
 * editor page, a Macro (FINDINGS section 23) */
struct fw_param {
    struct fw_string name;
    float value;
    uint16_t lo, hi;                        /* half floats */
    uint32_t flags;                         /* byte 2: stepped; byte 3: MIDI CC (0xff none) */
    uint32_t pad;
    uint32_t functor[2], manager, invoker;  /* the value display (a std::function) */
};
struct fw_cell { int32_t kind, index; };    /* {1, parameter} or {0, -1}: an empty cell */
struct fw_sub { struct fw_string name; struct fw_vector cells; };
struct fw_group { struct fw_string name, shortname; struct fw_vector subs, extra; };
struct fw_page { struct fw_string name, shortname; struct fw_vector extra, cells; };
struct fw_target { int32_t index; float amount; };
struct fw_macro { struct fw_string name; struct fw_vector targets; uint32_t flags; };
_Static_assert(sizeof(struct fw_param) == 0x38, "parameter entry");
_Static_assert(sizeof(struct fw_sub) == 0x24, "sub-screen");
_Static_assert(sizeof(struct fw_group) == 0x48, "group");
_Static_assert(sizeof(struct fw_page) == 0x48, "page");
_Static_assert(sizeof(struct fw_macro) == 0x28, "macro");

#define C_PARAMS            0x00u           /* container: the parameters (vector) */
#define C_MACROS            0x0Cu
#define C_MODEL             0x24u
#define C_MACRO_INIT        0x2Cu           /* a std::function the firmware calls with &Macros once it
                                             * has made the 8 of them: the model's Macro defaults */
#define C_CHANNELS          0x28u           /* the output channels: 1, or 2 (PERC's; the synth output reads it each block) */
#define C_GROUPS            0x44u
#define C_PAGES             0x50u

static const char *const wave_names[WT_SLOTS] = {
    "Formant", "Resonant", "Harmonic", "Square/PWM", "Sin/Tri/Saw/Sqr", "Sync", "FM", "Super Saw", "Saw",
};
static const char *const type_names[15] = {
    "LP Ladder 24", "LP OB 24", "LP OB 12", "LP SVF 24", "LP SVF 12", "HP OB 24", "HP OB 12",
    "HP SVF 24", "HP SVF 12", "BP OB 24", "BP OB 12", "BP SVF 24", "BP SVF 12", "Notch 24",
    "Notch 12",
};
static const char *const src_names[MOD_SRCS] = {
    "Amp Env", "Filter Env", "Mod Env", "LFO 1", "LFO 2", "Velocity", "Key",
};
static const char *const dest_names[MOD_DESTS] = {
    "Position", "Position 1", "Position 2", "Pitch", "Pitch 1", "Pitch 2", "Mix", "Cutoff", "Resonance",
    "Volume", "Noise",
};
static const char *const pol_names[2] = {"Bipolar", "Unipolar"};
static const char *const effect_names[FX_TYPES] = {"None", "Chorus"};

/* The value displays' std::function: an empty functor (as the firmware's own lambdas) */
static int fmt_manager(void **dest, void *const *source, int op)
{
    if (op == 1)                            /* __get_functor_ptr */
        *dest = (void *)source;
    return 0;
}

static uint32_t list_index(const float *value, uint32_t count)
{
    float v = *value;
    if (!(v >= 0.0f))
        return 0;
    uint32_t i = (uint32_t)(v + 0.5f);
    return i < count ? i : count - 1u;
}

/* invoker(std::string *ret, functor, the screen's accessor, float *value): a list's name */
#define FMT_LIST(name, table, count)                                                     \
    static struct fw_string *name(struct fw_string *ret, const void *functor, uint32_t x, \
                                  const float *value)                                  \
    {                                                                                   \
        (void)functor; (void)x;                                                         \
        str_init(ret, table[list_index(value, count)]);                                 \
        return ret;                                                                     \
    }
typedef struct fw_string *(*fmt_fn)(struct fw_string *, const void *, uint32_t, const float *);
FMT_LIST(fmt_wave, wave_names, WT_SLOTS)
FMT_LIST(fmt_type, type_names, 15)
FMT_LIST(fmt_src, src_names, MOD_SRCS)
FMT_LIST(fmt_dest, dest_names, MOD_DESTS)
FMT_LIST(fmt_pol, pol_names, 2)
FMT_LIST(fmt_effect, effect_names, FX_TYPES)

/* invoker for a 0..1 value shown as a percentage ("35 %") */
static struct fw_string *fmt_percent(struct fw_string *ret, const void *functor, uint32_t x,
                                     const float *value)
{
    (void)functor; (void)x;
    float v = *value;
    char buf[8];
    ((snprintf_fn)FW_SNPRINTF)(buf, sizeof buf, "%d %%", !(v >= 0.0f) ? 0 : v > 1.0f ? 100 : (int)(v * 100.0f + 0.5f));
    str_init(ret, buf);
    return ret;
}

/* invoker for the effect's Rate (0..1, 0.05-5 Hz) shown in Hz ("0.50 Hz") */
static struct fw_string *fmt_rate(struct fw_string *ret, const void *functor, uint32_t x,
                                  const float *value)
{
    (void)functor; (void)x;
    int c = (int)(rate_hz(*value) * 100.0f + 0.5f);
    char buf[12];
    ((snprintf_fn)FW_SNPRINTF)(buf, sizeof buf, "%d.%02d Hz", c / 100, c % 100);
    str_init(ret, buf);
    return ret;
}

static void set_display(struct fw_param *p, uint32_t invoker)
{
    if (p->manager)
        ((int (*)(void *, void *, int))p->manager)(p->functor, p->functor, 3);   /* __destroy_functor */
    p->functor[0] = p->functor[1] = 0;
    p->manager = (uint32_t)fmt_manager;
    p->invoker = invoker;
}

/* half floats: a list's top item */
#define H_0                 0x0000u
#define H_1                 0x3C00u         /* Polarity: Bipolar, Unipolar; Effect: None, Chorus */
#define H_6                 0x4600u         /* Src: 7 sources */
#define H_8                 0x4800u         /* Wave: 9 waves */
#define H_10                0x4900u         /* Dest: 11 destinations */
#define H_7                 0x4700u         /* PHASE's Wave: 8 shapes */
#define H_M1                0xBC00u         /* -1 */
#define H_48                0x5200u         /* DCO Env Amt: +-48 semitones */
#define H_M48               0xD200u

/* WAVE's names (0: VAP's) and Init values over VAP's entries */
struct wave_param { uint8_t index; const char *name; float value; };
static const struct wave_param wave_params[] = {
    {0, "Position 1", 0.6667f}, {1, "Position 2", 0.6667f},  /* a saw in Sin/Tri/Saw/Sqr */
    {2, "Wave 1", 4.0f}, {3, "Wave 2", 4.0f},              /* Sin/Tri/Saw/Sqr */
    {4, "Mix", 0.0f}, {5, "Tune 1", 0.0f}, {6, "Tune 2", 0.0f}, {7, "Detune", 8.0f},
    {8, "Finetune", 0.0f}, {9, "Reset 1", 0.0f}, {10, "Reset 2", 0.0f},
    {11, "Type", 0.0f}, {12, "Cutoff", 2400.0f}, {13, "Resonance", 0.2f}, {14, "Env Amt", 0.15f},
    {15, 0, 0.005f}, {16, 0, 0.8f}, {17, 0, 0.35f}, {18, 0, 0.5f},      /* the filter envelope */
    {19, 0, 0.005f}, {20, 0, 0.5f}, {21, 0, 0.8f}, {22, 0, 0.4f},      /* the amp envelope */
    {23, 0, 0.5f}, {24, 0, 1.0f}, {25, 0, 0.0f}, {26, 0, 0.5f},        /* the mod envelope */
    {27, 0, 1.0f}, {28, 0, 0.0f}, {29, 0, 2.0f}, {30, 0, 0.0f},        /* LFO 1: 1 Hz triangle, free */
    {31, 0, 0.25f}, {32, 0, 1.0f}, {33, 0, 2.0f}, {34, 0, 0.0f},       /* LFO 2: 0.25 Hz sine, free */
    {35, "Glide Mode", 0.0f}, {36, "Glide Time", 0.0f}, {37, "Key Track", 0.5f}, {38, "Drive", 0.0f},
    {40, "Digital", 0.0f}, {41, "Noise", 0.0f},
    {42, 0, (float)SRC_LFO1}, {43, 0, (float)DST_POSITION}, {44, 0, 0.0f},   /* routings to try: */
    {45, 0, (float)SRC_MOD_ENV}, {46, 0, (float)DST_POSITION}, {47, 0, 0.0f}, /* raise the Amt */
};

/* The screen's oscillator cells, in VAP's positions (3 to a row: oscillator 1, oscillator
 * 2, both), and the Macro editor's oscillator page; -1 is an empty cell */
static const int8_t osc_cells[] = {2, 3, 4, 0, 1, 40, 5, 6, 7, -1};
static const int8_t osc_page[] = {2, 0, 3, 1, 4, 5, 6, 8, 7, 41, 40, 9, 10};
/* The Modulation tab's envelope/LFO screens: VAP's cells, with each LFO's Polarity in the
 * empty cell just before its Sync */
static const int8_t lfo1_cells[] = {15, 16, 17, 18, 60, 30, 27, 28, 29};
static const int8_t lfo2_cells[] = {23, 24, 25, 26, -1, 61, 34, 31, 32, 33};
static const int8_t vap_lfo1_cells[] = {23, 24, 25, 26, -1, 30, 27, 28, 29};
static const int8_t vap_lfo2_cells[] = {15, 16, 17, 18, -1, -1, 34, 31, 32, 33};
/* The Edit Patch screen's LFO pages (a list; VAP's: Sync, Ratio, Waveform, Retrigger),
 * with Polarity last */
static const int8_t lfo1_page[] = {30, 27, 28, 29, 60};
static const int8_t lfo2_page[] = {34, 31, 32, 33, 61};
static const int8_t vap_lfo1_page[] = {-1, 30, 27, 28, 29};
static const int8_t vap_lfo2_page[] = {34, 31, 32, 33};
/* The Edit Patch screen's Env 1 and Env 2 pages: VAP's Env 1 is its filter envelope
 * (23-26), so WAVE's filter envelope (15-18) goes there, and its mod envelope (23-26) on
 * Env 2. (The engine keeps the slots: saved patches play as before.) */
static const int8_t fenv_page[] = {15, 16, 17, 18};
/* The Filter page and tab, with Drive (38) after Key Track */
static const int8_t filter_page[] = {12, 13, 11, 14, 37, 38};
static const int8_t filter_cells[] = {12, 13, 11, 14, -1, 37, 38, -1, -1};
static const int8_t vap_filter_page[] = {12, 13, 11, 14, 37};
static const int8_t vap_filter_cells[] = {12, 13, 11, 14, -1, 37, -1, -1, -1};
static const int8_t menv_page[] = {23, 24, 25, 26};
/* The effect: on the Voice page and tab, after VAP's three (the FX tab, with no cells in
 * VAP, is the stock instrument effects' and stays as it is) */
static const int8_t voice_page[] = {35, 36, 39, 62, 63, 64, 65};
static const int8_t vap_voice_page[] = {35, 36, 39};

static int cells_are(const struct fw_vector *v, const int8_t *cells, uint32_t n)
{
    if (vec_count(v, sizeof(struct fw_cell)) != n)
        return 0;
    const struct fw_cell *c = (const struct fw_cell *)v->begin;
    for (uint32_t k = 0; k < n; k++)
        if (c[k].index != cells[k] || c[k].kind != (cells[k] >= 0))
            return 0;
    return 1;
}

/* A cell vector set to cells; given more room from the heap when it has too little */
static int set_cells(struct fw_vector *v, const int8_t *cells, uint32_t n)
{
    uint32_t bytes = n * sizeof(struct fw_cell);
    if ((uint32_t)(v->cap - v->begin) < bytes) {
        uint8_t *b = (uint8_t *)((malloc_fn)FW_MALLOC)(bytes);
        if (!b)
            return 0;
        if (v->begin)
            ((delete_fn)FW_DELETE)(v->begin, (uint32_t)(v->cap - v->begin));
        v->begin = b;
        v->cap = b + bytes;
    }
    struct fw_cell *c = (struct fw_cell *)v->begin;
    for (uint32_t k = 0; k < n; k++) {
        c[k].kind = cells[k] >= 0;
        c[k].index = cells[k];
    }
    v->end = v->begin + n * sizeof(struct fw_cell);
    return 1;
}

/* WAVE's Macro defaults (the container's C_MACRO_INIT): VAP's, then a Attack, b
 * Decay/Release on the amp and filter envelopes, c Wave Position; d-f (the filter),
 * Velocity and Pressure stay as VAP's */
static void wave_macros(const void *functor, struct fw_vector *macros)
{
    ((void (*)(const void *, void *))FW_VAP_MACROS)(functor, macros);
    struct fw_macro *mc = (struct fw_macro *)macros->begin;
    if (vec_count(macros, sizeof *mc) != 8 || vec_count(&mc[0].targets, sizeof(struct fw_target)) != 2
        || vec_count(&mc[1].targets, sizeof(struct fw_target)) != 4 || mc[2].targets.begin)
        return;
    mc[0].targets.end = mc[0].targets.begin + sizeof(struct fw_target);     /* amp Attack (19) only */
    struct fw_target *t = (struct fw_target *)mc[1].targets.begin;
    t[0].index = 20; t[1].index = 16; t[2].index = 22; t[3].index = 18;      /* both Decays and Releases */
    struct fw_target *n = (struct fw_target *)((malloc_fn)FW_MALLOC)(2 * sizeof(struct fw_target));
    if (n) {
        n[0].index = 0; n[0].amount = 1.0f;                                  /* Position 1 and 2 */
        n[1].index = 1; n[1].amount = 1.0f;
        mc[2].targets.begin = (uint8_t *)n;
        mc[2].targets.end = mc[2].targets.cap = (uint8_t *)(n + 2);
        str_set(&mc[2].name, "Wave Position");
    }
}

/* VAP's patch as its builder leaves it, the layout WAVE's and PHASE's rewrites start from.
 * Checked first: anything unexpected leaves the patch VAP's (model 4). */
static int vap_layout_ok(uint8_t *c)
{
    struct fw_vector *pv = (struct fw_vector *)(c + C_PARAMS);
    struct fw_vector *gv = (struct fw_vector *)(c + C_GROUPS);
    struct fw_vector *qv = (struct fw_vector *)(c + C_PAGES);
    struct fw_param *p = (struct fw_param *)pv->begin;
    struct fw_group *g = (struct fw_group *)gv->begin;
    struct fw_page *q = (struct fw_page *)qv->begin;
    uint32_t *init = (uint32_t *)(c + C_MACRO_INIT);       /* functor[2], manager, invoker */
    if (vec_count(pv, sizeof *p) != VAP_PARAMS || vec_count(gv, sizeof *g) != 6 || vec_count(qv, sizeof *q) != 9
        || *(uint32_t *)(c + C_MODEL) != 4u || init[3] != FW_VAP_MACROS)
        return 0;
    if (!str_eq(&g[0].name, "Oscillator") || !str_eq(&g[2].name, "Modulation")
        || vec_count(&g[0].subs, sizeof(struct fw_sub)) != 2 || vec_count(&g[2].subs, sizeof(struct fw_sub)) != 3
        || !str_eq(&q[3].name, "Envelope 1") || !str_eq(&q[4].name, "Envelope 2") || !str_eq(&p[2].name, "PW 1")
        || !str_eq(&p[40].name, "Sync") || !str_eq(&p[42].name, "Src 1") || !str_eq(&p[59].name, "Amt 6"))
        return 0;
    struct fw_sub *osc = (struct fw_sub *)g[0].subs.begin;
    struct fw_sub *mod = (struct fw_sub *)g[2].subs.begin;
    if ((uint32_t)(osc[0].cells.cap - osc[0].cells.begin) < sizeof osc_cells * sizeof(struct fw_cell)
        || (uint32_t)(q[0].cells.cap - q[0].cells.begin) < sizeof osc_page * sizeof(struct fw_cell)
        || !cells_are(&mod[1].cells, vap_lfo1_cells, sizeof vap_lfo1_cells)
        || !cells_are(&mod[2].cells, vap_lfo2_cells, sizeof vap_lfo2_cells)
        || !str_eq(&q[5].name, "LFO 1") || !str_eq(&q[6].name, "LFO 2")
        || !cells_are(&q[5].cells, vap_lfo1_page, sizeof vap_lfo1_page)
        || !cells_are(&q[6].cells, vap_lfo2_page, sizeof vap_lfo2_page)
        || !cells_are(&q[3].cells, menv_page, sizeof menv_page) || !cells_are(&q[4].cells, fenv_page, sizeof fenv_page)
        || !str_eq(&g[1].name, "Filter") || vec_count(&g[1].subs, sizeof(struct fw_sub)) != 1
        || !cells_are(&((struct fw_sub *)g[1].subs.begin)[0].cells, vap_filter_cells, sizeof vap_filter_cells)
        || !str_eq(&q[1].name, "Filter") || !cells_are(&q[1].cells, vap_filter_page, sizeof vap_filter_page)
        || !str_eq(&p[38].name, "Mix Env Amt")
        || !str_eq(&g[5].name, "Voice") || vec_count(&g[5].subs, sizeof(struct fw_sub)) != 1
        || !cells_are(&((struct fw_sub *)g[5].subs.begin)[0].cells, vap_voice_page, sizeof vap_voice_page)
        || !str_eq(&q[8].name, "Voice") || !cells_are(&q[8].cells, vap_voice_page, sizeof vap_voice_page)
        || *(uint32_t *)(c + C_CHANNELS) != 1u)
        return 0;
    return 1;
}

/* The own six after VAP's 60, the same in WAVE and PHASE: the LFOs' Polarity, Bipolar (0)
 * or Unipolar (1), then the effect's (wt_fx.h). First, as the firmware's own append makes
 * the entries and may move the vector. The lists are stepped like Type, the others plain
 * like VAP's Mix Env Amt; none has a CC or a rebuild on change. Returns the entries, or 0. */
static struct fw_param *append_own(uint8_t *c)
{
    struct fw_vector *pv = (struct fw_vector *)(c + C_PARAMS);
    ((void (*)(struct fw_vector *, uint32_t))FW_PARAMS_APPEND)(pv, CUSTOM_PARAMS - VAP_PARAMS);
    if (!pv->begin || vec_count(pv, sizeof(struct fw_param)) != CUSTOM_PARAMS)
        return 0;
    struct fw_param *p = (struct fw_param *)pv->begin;
    for (uint32_t k = VAP_PARAMS; k < CUSTOM_PARAMS; k++) {
        static const char *const names[CUSTOM_PARAMS - VAP_PARAMS] = {
            "Polarity", "Polarity", "Effect", "Rate", "Depth", "Mix",
        };
        static const fmt_fn displays[CUSTOM_PARAMS - VAP_PARAMS] = {
            fmt_pol, fmt_pol, fmt_effect, fmt_rate, fmt_percent, fmt_percent,
        };
        uint32_t list = k < FX_FIRST + FX_RATE;
        str_set(&p[k].name, names[k - VAP_PARAMS]);
        p[k].value = k < FX_FIRST ? 0.0f : fx_defaults[k - FX_FIRST];
        p[k].lo = H_0;
        p[k].hi = H_1;
        p[k].flags = list ? p[11].flags : p[38].flags;
        p[k].pad = 0;
        set_display(&p[k], (uint32_t)displays[k - VAP_PARAMS]);
    }
    return p;
}

/* VAP's patch -> WAVE's. The screen keeps VAP's tabs and cells (Oscillator, Filter,
 * Modulation, Matrix, FX, Voice), with WAVE's oscillator cells and names. WAVE's filter
 * envelope is VAP's Envelope 2 (15-18) and its mod envelope VAP's Envelope 1 (23-26); the
 * pages and tabs show the filter envelope first, as VAP shows its own (23-26: VAP's filter
 * envelope). */
static void wave_rewrite(uint8_t *c)
{
    if (!vap_layout_ok(c))
        return;
    struct fw_group *g = (struct fw_group *)((struct fw_vector *)(c + C_GROUPS))->begin;
    struct fw_page *q = (struct fw_page *)((struct fw_vector *)(c + C_PAGES))->begin;
    uint32_t *init = (uint32_t *)(c + C_MACRO_INIT);
    struct fw_sub *osc = (struct fw_sub *)g[0].subs.begin;
    struct fw_sub *mod = (struct fw_sub *)g[2].subs.begin;
    struct fw_param *p = append_own(c);
    if (!p)
        return;

    /* the parameters */
    uint32_t stepped = p[11].flags;         /* Type's: a stepped list, no CC */
    for (uint32_t k = 0; k < sizeof wave_params / sizeof wave_params[0]; k++) {
        struct fw_param *e = &p[wave_params[k].index];
        if (wave_params[k].name)
            str_set(&e->name, wave_params[k].name);
        e->value = wave_params[k].value;
    }
    for (uint32_t k = 2; k <= 3; k++) {     /* Wave 1, Wave 2: by name */
        p[k].lo = H_0;
        p[k].hi = H_8;
        p[k].flags = stepped;
        set_display(&p[k], (uint32_t)fmt_wave);
    }
    set_display(&p[11], (uint32_t)fmt_type);
    p[38].lo = H_0;                         /* Drive: 0-100 % (VAP's -2..2 Mix Env Amt, unused) */
    p[38].hi = H_1;
    set_display(&p[38], (uint32_t)fmt_percent);
    for (uint32_t s = 0; s < MOD_SLOTS; s++) {  /* the Mod Matrix's sources and destinations */
        p[42 + 3 * s].hi = H_6;
        set_display(&p[42 + 3 * s], (uint32_t)fmt_src);
        p[43 + 3 * s].hi = H_10;
        set_display(&p[43 + 3 * s], (uint32_t)fmt_dest);
    }

    /* the screen and the Macro editor's pages */
    set_cells(&osc[0].cells, osc_cells, sizeof osc_cells);
    str_set(&mod[1].name, "Flt Env/LFO 1");
    str_set(&mod[2].name, "Mod Env/LFO 2");
    set_cells(&mod[1].cells, lfo1_cells, sizeof lfo1_cells);
    set_cells(&mod[2].cells, lfo2_cells, sizeof lfo2_cells);
    set_cells(&q[5].cells, lfo1_page, sizeof lfo1_page);
    set_cells(&q[6].cells, lfo2_page, sizeof lfo2_page);   /* one more cell: from the heap */
    set_cells(&q[0].cells, osc_page, sizeof osc_page);
    set_cells(&((struct fw_sub *)g[1].subs.begin)[0].cells, filter_cells, sizeof filter_cells);
    set_cells(&q[1].cells, filter_page, sizeof filter_page);    /* one more cell: from the heap */
    set_cells(&q[3].cells, fenv_page, sizeof fenv_page);    /* the Env 1 tile */
    set_cells(&q[4].cells, menv_page, sizeof menv_page);    /* the Env 2 tile */
    str_set(&q[3].name, "Filter Envelope");
    str_set(&q[3].shortname, "Flt Env");
    str_set(&q[4].name, "Mod Envelope");
    str_set(&q[4].shortname, "Mod Env");
    set_cells(&q[8].cells, voice_page, sizeof voice_page);  /* four more cells: from the heap */
    set_cells(&((struct fw_sub *)g[5].subs.begin)[0].cells, voice_page, sizeof voice_page);
    *(uint32_t *)(c + C_CHANNELS) = 2u;     /* stereo: the effect writes L and R */

    /* the Macros, once the firmware has made them */
    init[2] = (uint32_t)fmt_manager;        /* an empty functor, as VAP's */
    init[3] = (uint32_t)wave_macros;

    *(uint32_t *)(c + C_MODEL) = MODEL_WAVE;
}

/* WAVE's parameter builder (the descriptor's std::function calls it with the new patch
 * and its container): VAP's, rewritten */
static void wave_builder(void *patch, uint8_t *container)
{
    /* A new patch's vector made 66 long first: VAP's builder trims it to its 60 and keeps
     * the room, so the own six go in without a new allocation. (Growing 60 to 66 would
     * double the capacity: a 6.7 KB allocation, kept for the patch's life.) */
    struct fw_vector *pv = (struct fw_vector *)(container + C_PARAMS);
    if (pv->begin == pv->end)
        ((void (*)(struct fw_vector *, uint32_t))FW_PARAMS_APPEND)(pv, CUSTOM_PARAMS);
    ((void (*)(void *, void *))FW_VAP_BUILDER)(patch, container);
    wave_rewrite(container);
}

/* ---- PHASE: the descriptor and its builder ------------------------------------------- */

static const char *const shape_names[PH_SHAPES] = {
    "Saw", "Square", "Pulse", "Double Sine", "Saw-Pulse", "Reso I", "Reso II", "Reso III",
};
static const char *const pair_names[1 + PH_SHAPES] = {
    "Off", "Saw", "Square", "Pulse", "Double Sine", "Saw-Pulse", "Reso I", "Reso II", "Reso III",
};
static const char *const ph_src_names[PH_SRCS] = {
    "DCA Env", "DCW Env", "DCO Env", "LFO 1", "LFO 2", "Velocity", "Key",
};
static const char *const ph_dest_names[PH_DESTS] = {
    "Pitch", "Pitch 1", "Pitch 2", "DCW", "DCW 1", "DCW 2", "Mix", "Volume", "Detune",
};
static const char *const onoff_names[2] = {"Off", "On"};
FMT_LIST(fmt_shape, shape_names, PH_SHAPES)
FMT_LIST(fmt_pair, pair_names, 1 + PH_SHAPES)
FMT_LIST(fmt_ph_src, ph_src_names, PH_SRCS)
FMT_LIST(fmt_ph_dest, ph_dest_names, PH_DESTS)
FMT_LIST(fmt_onoff, onoff_names, 2)

/* invoker for whole semitones ("-12 st") */
static struct fw_string *fmt_semis(struct fw_string *ret, const void *functor, uint32_t x,
                                   const float *value)
{
    (void)functor; (void)x;
    float v = clampf(*value, -48.0f, 48.0f);
    char buf[8];
    ((snprintf_fn)FW_SNPRINTF)(buf, sizeof buf, "%d st", (int)(v + (v < 0.0f ? -0.5f : 0.5f)));
    str_init(ret, buf);
    return ret;
}

/* PHASE's names (0: VAP's) and Init values over VAP's entries */
static const struct wave_param phase_params[] = {
    {0, "Wave 1+", 0.0f}, {1, "Wave 2+", 0.0f},            /* Off: one shape per DCO */
    {2, "Wave 1", 0.0f}, {3, "Wave 2", 0.0f},              /* Saw */
    {4, "Mix", 0.0f}, {5, "Tune 1", 0.0f}, {6, "Tune 2", 0.0f}, {7, "Detune", 8.0f},
    {8, "Finetune", 0.0f}, {9, "Reset 1", 0.0f}, {10, "Reset 2", 0.0f},
    {11, "Ring Mod", 0.0f}, {12, "DCW 1", 0.5f}, {13, "DCW 2", 0.5f}, {14, "Env Amt 1", 0.3f},
    {15, 0, 0.005f}, {16, 0, 0.3f}, {17, 0, 0.0f}, {18, 0, 0.3f},      /* the DCO (pitch) envelope */
    {19, 0, 0.005f}, {20, 0, 0.5f}, {21, 0, 0.8f}, {22, 0, 0.4f},      /* the DCA envelope */
    {23, 0, 0.005f}, {24, 0, 0.6f}, {25, 0, 0.3f}, {26, 0, 0.5f},      /* the DCW envelope */
    {27, 0, 1.0f}, {28, 0, 0.0f}, {29, 0, 2.0f}, {30, 0, 0.0f},        /* LFO 1: 1 Hz triangle, free */
    {31, 0, 0.25f}, {32, 0, 1.0f}, {33, 0, 2.0f}, {34, 0, 0.0f},       /* LFO 2: 0.25 Hz sine, free */
    {35, "Glide Mode", 0.0f}, {36, "Glide Time", 0.0f}, {37, "Key Follow", 0.0f}, {38, "Env Amt 2", 0.3f},
    {40, "Noise Mod", 0.0f}, {41, "DCO Env Amt", 0.0f},
    {42, 0, (float)PH_SRC_LFO1}, {43, 0, (float)PH_DST_PITCH}, {44, 0, 0.0f},   /* routings to try: */
    {45, 0, (float)PH_SRC_LFO2}, {46, 0, (float)PH_DST_DCW}, {47, 0, 0.0f},     /* raise the Amt */
};

/* PHASE's screen (the Synth Parameters tabs) and Edit Patch pages; -1 is an empty cell */
static const int8_t ph_osc_cells[] = {2, 3, 4, 0, 1, 11, 5, 6, 7, -1};       /* DCO (1/2) */
static const int8_t ph_osc2_cells[] = {9, 10, 40, -1, -1, 8, -1, -1};        /* DCO (2/2) */
static const int8_t vap_osc2_cells[] = {9, 10, 41, -1, -1, 8, -1, -1};
static const int8_t ph_dcw_cells[] = {12, 13, -1, 14, 38, 37, -1, -1, -1};
static const int8_t ph_lfo1_cells[] = {23, 24, 25, 26, 60, 30, 27, 28, 29};  /* DCW Env/LFO 1 */
static const int8_t ph_lfo2_cells[] = {15, 16, 17, 18, 41, 61, 34, 31, 32, 33};  /* DCO Env/LFO 2 */
static const int8_t ph_osc_page[] = {2, 0, 3, 1, 4, 5, 6, 8, 7, 9, 10, 11, 40};
static const int8_t ph_dcw_page[] = {12, 13, 14, 38, 37};
static const int8_t ph_penv_page[] = {15, 16, 17, 18, 41};

/* PHASE's Macro defaults: VAP's, then a Attack (the DCA's), b Decay/Release (the DCA's and
 * the DCW envelope's, as VAP's), c DCW, d DCW Env Amt, e Detune, f Mix; Velocity and
 * Pressure stay as VAP's */
static void phase_macros(const void *functor, struct fw_vector *macros)
{
    ((void (*)(const void *, void *))FW_VAP_MACROS)(functor, macros);
    struct fw_macro *mc = (struct fw_macro *)macros->begin;
    if (vec_count(macros, sizeof *mc) != 8 || vec_count(&mc[0].targets, sizeof(struct fw_target)) != 2
        || vec_count(&mc[1].targets, sizeof(struct fw_target)) != 4 || mc[2].targets.begin
        || vec_count(&mc[3].targets, sizeof(struct fw_target)) != 1
        || vec_count(&mc[4].targets, sizeof(struct fw_target)) != 1
        || vec_count(&mc[5].targets, sizeof(struct fw_target)) != 1)
        return;
    mc[0].targets.end = mc[0].targets.begin + sizeof(struct fw_target);     /* the DCA's Attack (19) only */
    struct fw_target *n = (struct fw_target *)((malloc_fn)FW_MALLOC)(2 * sizeof(struct fw_target));
    if (n) {
        n[0].index = 12; n[0].amount = 1.0f;                                 /* DCW 1 and 2 */
        n[1].index = 13; n[1].amount = 1.0f;
        mc[2].targets.begin = (uint8_t *)n;
        mc[2].targets.end = mc[2].targets.cap = (uint8_t *)(n + 2);
        str_set(&mc[2].name, "DCW");
    }
    n = (struct fw_target *)((malloc_fn)FW_MALLOC)(2 * sizeof(struct fw_target));
    if (n) {                                                                 /* over VAP's Cutoff (12) */
        struct fw_vector *d = &mc[3].targets;
        n[0].index = 14; n[0].amount = ((struct fw_target *)d->begin)->amount;  /* Env Amt 1 and 2 */
        n[1].index = 38; n[1].amount = n[0].amount;
        ((delete_fn)FW_DELETE)(d->begin, (uint32_t)(d->cap - d->begin));
        d->begin = (uint8_t *)n;
        d->end = d->cap = (uint8_t *)(n + 2);
        str_set(&mc[3].name, "DCW Env Amt");
    }
    ((struct fw_target *)mc[4].targets.begin)->index = 7;                    /* VAP's Resonance (13) */
    str_set(&mc[4].name, "Detune");
    ((struct fw_target *)mc[5].targets.begin)->index = 4;                    /* VAP's Filter Env Amount (14) */
    str_set(&mc[5].name, "Mix");
}

/* VAP's patch -> PHASE's (docs/PD_SYNTH_PLAN.md section 4). The screen keeps VAP's tabs,
 * named DCO, DCW, Modulation (DCA, DCW Env/LFO 1, DCO Env/LFO 2), Matrix, FX and Voice.
 * The DCW envelope is VAP's Envelope 1 (23-26, VAP's filter envelope), the DCO (pitch)
 * envelope VAP's Envelope 2 (15-18). */
static void phase_rewrite(uint8_t *c)
{
    if (!vap_layout_ok(c))
        return;
    struct fw_group *g = (struct fw_group *)((struct fw_vector *)(c + C_GROUPS))->begin;
    struct fw_page *q = (struct fw_page *)((struct fw_vector *)(c + C_PAGES))->begin;
    uint32_t *init = (uint32_t *)(c + C_MACRO_INIT);
    struct fw_sub *osc = (struct fw_sub *)g[0].subs.begin;
    struct fw_sub *mod = (struct fw_sub *)g[2].subs.begin;
    if (!cells_are(&osc[1].cells, vap_osc2_cells, sizeof vap_osc2_cells))
        return;
    struct fw_param *p = append_own(c);
    if (!p)
        return;

    /* the parameters */
    uint32_t stepped = p[11].flags;         /* Type's: a stepped list, no CC */
    uint32_t plain = p[41].flags;           /* VAP's Noise: plain, no CC */
    for (uint32_t k = 0; k < sizeof phase_params / sizeof phase_params[0]; k++) {
        struct fw_param *e = &p[phase_params[k].index];
        if (phase_params[k].name)
            str_set(&e->name, phase_params[k].name);
        e->value = phase_params[k].value;
    }
    for (uint32_t k = 0; k <= 3; k++) {     /* Wave 1+, Wave 2+ (Off and the shapes), Wave 1, Wave 2 */
        p[k].lo = H_0;
        p[k].hi = k < 2 ? H_8 : H_7;
        p[k].flags = stepped;
        set_display(&p[k], (uint32_t)(k < 2 ? fmt_pair : fmt_shape));
    }
    p[11].hi = H_1;                         /* Ring Mod: Off/On (VAP's Type) */
    set_display(&p[11], (uint32_t)fmt_onoff);
    for (uint32_t k = 12; k <= 13; k++) {   /* DCW 1, DCW 2: 0-100 %, keeping VAP's CCs 74 and 71 */
        p[k].lo = H_0;
        p[k].hi = H_1;
        p[k].flags = (p[k].flags & 0xFF000000u) | (plain & 0x00FFFFFFu);
        set_display(&p[k], (uint32_t)fmt_percent);
    }
    p[37].lo = p[38].lo = H_M1;             /* Key Follow, Env Amt 2: +-100 % (VAP's +-200 %) */
    p[37].hi = p[38].hi = H_1;
    p[38].flags = 0xFF000000u | (p[14].flags & 0x00FFFFFFu);    /* as Env Amt 1, without its CC */
    p[40].lo = H_0;                         /* Noise Mod: 0-100 % (VAP's Sync, a list) */
    p[40].hi = H_1;
    p[40].flags = plain;
    set_display(&p[40], (uint32_t)fmt_percent);
    p[41].lo = H_M48;                       /* DCO Env Amt: +-48 semitones, whole ones (VAP's Noise) */
    p[41].hi = H_48;
    p[41].flags = stepped;
    set_display(&p[41], (uint32_t)fmt_semis);
    for (uint32_t s = 0; s < MOD_SLOTS; s++) {  /* the Mod Matrix's sources and destinations */
        p[42 + 3 * s].hi = H_6;
        set_display(&p[42 + 3 * s], (uint32_t)fmt_ph_src);
        p[43 + 3 * s].hi = H_8;
        set_display(&p[43 + 3 * s], (uint32_t)fmt_ph_dest);
    }

    /* the screen */
    str_set(&g[0].name, "DCO");
    set_cells(&osc[0].cells, ph_osc_cells, sizeof ph_osc_cells);
    set_cells(&osc[1].cells, ph_osc2_cells, sizeof ph_osc2_cells);
    str_set(&g[1].name, "DCW");
    set_cells(&((struct fw_sub *)g[1].subs.begin)[0].cells, ph_dcw_cells, sizeof ph_dcw_cells);
    str_set(&mod[0].name, "DCA");
    str_set(&mod[1].name, "DCW Env/LFO 1");
    str_set(&mod[2].name, "DCO Env/LFO 2");
    set_cells(&mod[1].cells, ph_lfo1_cells, sizeof ph_lfo1_cells);
    set_cells(&mod[2].cells, ph_lfo2_cells, sizeof ph_lfo2_cells);
    set_cells(&((struct fw_sub *)g[5].subs.begin)[0].cells, voice_page, sizeof voice_page);

    /* the Edit Patch pages (their tiles' labels: mt_syn_tiles) */
    str_set(&q[0].name, "DCO");
    str_set(&q[0].shortname, "DCO");
    set_cells(&q[0].cells, ph_osc_page, sizeof ph_osc_page);
    str_set(&q[1].name, "DCW");
    set_cells(&q[1].cells, ph_dcw_page, sizeof ph_dcw_page);
    str_set(&q[2].name, "DCA");
    str_set(&q[2].shortname, "DCA");
    str_set(&q[3].name, "DCW Envelope");    /* 23-26, as VAP's Envelope 1 */
    str_set(&q[3].shortname, "DCW Env");
    str_set(&q[4].name, "DCO Envelope");
    str_set(&q[4].shortname, "DCO Env");
    set_cells(&q[4].cells, ph_penv_page, sizeof ph_penv_page);  /* one more cell: from the heap */
    set_cells(&q[5].cells, lfo1_page, sizeof lfo1_page);
    set_cells(&q[6].cells, lfo2_page, sizeof lfo2_page);       /* one more cell: from the heap */
    set_cells(&q[8].cells, voice_page, sizeof voice_page);     /* four more cells: from the heap */
    *(uint32_t *)(c + C_CHANNELS) = 2u;     /* stereo: the effect writes L and R */

    /* the Macros, once the firmware has made them */
    init[2] = (uint32_t)fmt_manager;
    init[3] = (uint32_t)phase_macros;

    *(uint32_t *)(c + C_MODEL) = MODEL_PHASE;
}

/* PHASE's parameter builder: VAP's, rewritten (as WAVE's) */
static void phase_builder(void *patch, uint8_t *container)
{
    struct fw_vector *pv = (struct fw_vector *)(container + C_PARAMS);
    if (pv->begin == pv->end)
        ((void (*)(struct fw_vector *, uint32_t))FW_PARAMS_APPEND)(pv, CUSTOM_PARAMS);
    ((void (*)(void *, void *))FW_VAP_BUILDER)(patch, container);
    phase_rewrite(container);
}

/* A model's descriptor into the registry: its name, a parameter builder, VAP's voice
 * factory */
static void add_model(void *registry, uint32_t id, const char *name, void (*builder)(void *, uint8_t *))
{
    uint32_t *d = (uint32_t *)((malloc_fn)FW_MALLOC)(0x38);
    if (!d)
        return;
    for (uint32_t k = 0; k < 0x38 / 4; k++)
        d[k] = 0;
    str_init((struct fw_string *)d, name);
    d[6] = (uint32_t)builder;               /* +0x18: the parameter builder */
    d[8] = FW_FN_MANAGER;
    d[9] = FW_FN_INVOKER;
    d[12] = FW_VAP_NEW_MANAGER;             /* +0x28: the voice factory, VAP's */
    d[13] = FW_VAP_NEW_INVOKER;
    uint32_t *held = d;
    ((void (*)(void *, uint32_t, uint32_t **))FW_REGISTRY_INSERT)(registry, id, &held);
    if (held)                               /* not taken: the id is someone's already */
        ((delete_fn)FW_DELETE)(d, 0x38);
}

/* replaces "bl 0x60140150" at 0x60126b30 (the synth manager's constructor; chained):
 * the stock models, then WAVE as model 3 and PHASE as model 7 */
void mt_syn_registry(void *registry)
{
    ((void (*)(void *))mt_syn_next_registry)(registry);
    add_model(registry, MODEL_WAVE, "WAVE", wave_builder);
    add_model(registry, MODEL_PHASE, "PHASE", phase_builder);
}

/* ---- the Model list ------------------------------------------------------------------ */

/* replaces "bl disp_ctrl_set_data" at 0x6011b0ba (the synth settings screen's Model
 * list; chained): with a swap, the list gets the patch's image ids */
void mt_syn_list_data(void *mgr, int id, uint8_t *data)
{
    if (SY->magic == SY_MAGIC && (SY->applied < MODELS || SY->ph_applied < MODELS))
        *(int32_t **)(data + 0x18) = SY->model_ids;
    ((void (*)(void *, int, void *))mt_syn_next_list_data)(mgr, id, data);
}

/* replaces "bl 0x6010645c" at 0x60106606 (a list control's image items; chained): the
 * stock images, then "WAVE" or "PHASE" as text in the rows the patch's ids mark */
void mt_syn_list_images(uint8_t *ctrl, int x0, int x1, int y, int step)
{
    ((void (*)(uint8_t *, int, int, int, int))mt_syn_next_list_images)(ctrl, x0, x1, y, step);
    const uint8_t *data = *(uint8_t *const *)(ctrl + 0x30);
    if (SY->magic != SY_MAGIC || !data || *(const int32_t *const *)(data + 0x18) != SY->model_ids)
        return;
    uint32_t count = *(const uint16_t *)(data + 2), rows = *(const uint16_t *)(data + 4);
    if (rows > count)
        rows = count;
    const uint8_t *font = *(uint8_t *const *)(ctrl + 0x14);
    int w = x1 - x0;
    if (w < 0)
        w += 1;
    for (uint32_t r = 0; r < rows; r++, y += step) {
        int k = *(const int32_t *)(ctrl + 0x3c) + (int)r;
        if (k < 0)
            k = 0;
        if (k > (int)count - 1)
            k = (int)count - 1;
        const char *name = k >= (int)MODELS ? 0 : SY->model_ids[k] == LIST_TEXT_ID ? "WAVE"
                           : SY->model_ids[k] == LIST_TEXT_PHASE ? "PHASE" : 0;
        if (name && font)
            ((void (*)(int, int, int, uint32_t, const char *))FW_EVE_TEXT)(
                x0 + (w >> 1), (int16_t)y, *(const int16_t *)(font + 0x98), EVE_OPT_CENTER, name);
    }
}

/* replaces "bl disp_ctrl_set_text" at 0x6011ea58 (the Edit Patch screen's logo label, its
 * text set to ""; chained): for a WAVE or PHASE synth, no logo (the label's icon id 0) and
 * the engine's name as its text. The label's data (its icons, 6 bytes each, the id at +4) was set just before. */
#define FW_SELECTED_INSTR   (*(volatile uint8_t *)0x202046C5u)  /* 64-66: S1-S3 */
#define MGR_CONTROL(m, id)  (*(uint8_t **)((uint8_t *)(m) + 0x10u + 4u * (uint32_t)(id)))
#define LABEL_ICONS         0x30u           /* label control: its data (set_data) */

void mt_syn_patch_label(void *mgr, int id, const char *text)
{
    uint32_t slot = (uint32_t)FW_SELECTED_INSTR - 64u;
    uint32_t engine = slot < SLOTS ? engine_of_model(slot_word(slot, 0)) : ENGINE_NONE;
    if (engine != ENGINE_NONE && id >= 0 && id <= 0x40) {
        uint8_t *ctrl = MGR_CONTROL(mgr, id);
        uint8_t *icons = ctrl ? *(uint8_t **)(ctrl + LABEL_ICONS) : 0;
        if (icons)
            icons[4] = 0;                   /* no icon */
        text = engine == ENGINE_PHASE ? "PHASE" : "WAVE";
    }
    ((void (*)(void *, int, const char *))mt_syn_next_patch_label)(mgr, id, text);
}

/* replaces "bl 0x6011d628" at 0x6011e924 (the Edit Patch screen's tiles: each page's
 * label and icon, from keywords in its name; chained): for a PHASE synth, the first five
 * tiles' labels and icons by page (docs/PD_SYNTH_PLAN.md section 4.1). The tiles control
 * reads the buffers each time it draws. */
#define TILE_LABELS         0x448u          /* screen: a pointer to the labels' pointers (8-byte buffers) */
#define TILE_COUNT          0x450u          /* the tile count (byte) */
#define TILE_KINDS          0x42Cu          /* the icon kinds (bytes) */
#define PHASE_PAGES         9u

void mt_syn_tiles(uint8_t *scr, uint32_t flag)
{
    static const char *const labels[] = {"DCO", "DCW", "DCA", "DCW Env", "DCO Env"};
    static const uint8_t kinds[] = {0x17, 0x12, 0x11, 0x11, 0x11};   /* Osc's, Filter's, the envelopes' */
    ((void (*)(uint8_t *, uint32_t))mt_syn_next_tiles)(scr, flag);
    uint32_t slot = (uint32_t)FW_SELECTED_INSTR - 64u;
    char **label = *(char ***)(scr + TILE_LABELS);
    if (slot >= SLOTS || slot_word(slot, 0) != MODEL_PHASE || scr[TILE_COUNT] != PHASE_PAGES || !label)
        return;
    for (uint32_t i = 0; i < sizeof kinds; i++) {
        char *d = label[i];
        if (!d)
            continue;
        uint32_t n = 0;
        for (; labels[i][n] && n < 7; n++)
            d[n] = labels[i][n];
        d[n] = 0;
        scr[TILE_KINDS + i] = kinds[i];
    }
}

/* ---- loading a WAVE or PHASE patch --------------------------------------------------------------- */

typedef int (*set_value_fn)(void *patch, uint32_t index, const float *value, uint32_t source);
#define SET_SOURCE_LOAD     2u              /* what the loader passes */

/* A WAVE or PHASE patch: the patch class, the engine's model id, and its 66 values */
static float *custom_values(uint8_t *patch)
{
    if (!patch || *(const uint32_t *)patch != FW_PATCH_VTABLE
        || engine_of_model(*(const uint32_t *)(patch + PATCH_MODEL)) == ENGINE_NONE)
        return 0;
    const struct fw_vector *v = (const struct fw_vector *)(patch + PATCH_VALUES);
    return vec_count(v, sizeof(float)) == CUSTOM_PARAMS ? (float *)v->begin : 0;
}

/* The own six (the Polarity switches, the effect's) through the patch's own set_value,
 * as the loader sets values (it also hands them to the voices) */
static void set_own(uint8_t *patch, const float *own)
{
    set_value_fn set = (set_value_fn)(*(uint32_t *const *)patch)[PATCH_SET_VALUE / 4u];
    for (uint32_t k = 0; k < CUSTOM_PARAMS - VAP_PARAMS; k++)
        set(patch, VAP_PARAMS + k, &own[k], SET_SOURCE_LOAD);
}

/* replaces the patch storage's "load into a patch" (vtable 0x60062fd4 + 0x0c, 0x60124f39;
 * chained): (storage, &path, patch, 1), 1 when loaded. A slot's Workspace patch and
 * Patches/<model>/<name>.patch both load through it. The loader sets as many values as
 * the file holds, so a WAVE patch saved before WAVE's own parameters (60 values: sy3; 62:
 * sy4-sy6, without the effect) would keep them as they were: they go to their defaults
 * (Bipolar, Effect None) first, and back if nothing loads. */
int mt_syn_patch_load(void *storage, const void *path, uint8_t *patch, uint32_t flag)
{
    typedef int (*load_fn)(void *, const void *, uint8_t *, uint32_t);
    const float *v = custom_values(patch);
    float old[CUSTOM_PARAMS - VAP_PARAMS];
    float own[CUSTOM_PARAMS - VAP_PARAMS] = {0.0f, 0.0f};  /* Bipolar */
    for (uint32_t k = 0; k < FX_PARAMS; k++)
        own[FX_FIRST - VAP_PARAMS + k] = fx_defaults[k];
    if (v) {
        for (uint32_t k = 0; k < CUSTOM_PARAMS - VAP_PARAMS; k++)
            old[k] = v[VAP_PARAMS + k];
        set_own(patch, own);
    }
    int ok = ((load_fn)mt_syn_next_patch_load)(storage, path, patch, flag);
    if (v && !ok && custom_values(patch))
        set_own(patch, old);
    return ok;
}

/* ---- the Config menu ------------------------------------------------------------ */

#define MENU_ITEM_SIZE      0x20u           /* fields up to +0x18 */
#define MENU_FOLDER_SIZE    0x20u           /* fields up to +0x1e */
#define MENU_OPTIONS        4u              /* stock option item (e.g. Anti-aliasing) */

/* option item (type 4): value pointer, option count, option names, and a callback that
 * the menu's apply (cfg_menu_apply, 0x60118658) calls after storing the choice, without
 * checking it for NULL. */
struct menu_options { volatile uint8_t *value; uint32_t count; const char *const *names; void_fn on_apply; };

/* Each item lists Off and the models the other item doesn't replace. The menu edits a
 * position in that list (menu_pos, ph_menu_pos), which the item's callback turns into the
 * choice (menu, ph_menu). Its descriptor (from the heap, made with the item) names the
 * list it shows; the editor reads the count and names each time it opens, and the value
 * text is names[position], so every list has a sixth entry (0: the menu's default text). */
static const char *const replace_names[1 + MODELS] = {"Off", "ACD", "FAT", "VAP", "WTFM", "PERC"};
static const char *const replace_without[MODELS][1 + MODELS] = {
    {"Off", "FAT", "VAP", "WTFM", "PERC", 0},       /* the other item replaces ACD */
    {"Off", "ACD", "VAP", "WTFM", "PERC", 0},
    {"Off", "ACD", "FAT", "WTFM", "PERC", 0},
    {"Off", "ACD", "FAT", "VAP", "PERC", 0},
    {"Off", "ACD", "FAT", "VAP", "WTFM", 0},
};

/* the model index a descriptor's list leaves out, or REPLACE_NONE */
static uint32_t list_without(const struct menu_options *d)
{
    for (uint32_t k = 0; k < MODELS; k++)
        if (d->names == replace_without[k])
            return k;
    return REPLACE_NONE;
}

static uint8_t pos_of(uint32_t menu, uint32_t without)
{
    uint32_t k = replace_of(menu);
    if (k == REPLACE_NONE || k == without)
        return 0;
    return (uint8_t)(1u + k - (without < MODELS && k > without));
}

static uint8_t menu_of(uint32_t pos, uint32_t without)
{
    if (pos == 0 || pos > MODELS)
        return 0;
    uint32_t k = pos - 1u;
    if (without < MODELS && k >= without)
        k++;
    return (uint8_t)(k < MODELS ? k + 1u : 0u);
}

static void option_applied(void);
static void ph_option_applied(void);
static const struct menu_options replace_desc = {&SY->menu_pos, 1 + MODELS, replace_names, option_applied};
static const char replace_help[] =
    "Plays WAVE, a wavetable synth, in place of one synth model in this project. The "
    "Instrument screen's Model list then shows WAVE where that model was. Synths set to "
    "that model start from WAVE's Init patch.";
static const struct menu_options ph_replace_desc = {&SY->ph_menu_pos, 1 + MODELS, replace_names, ph_option_applied};
static const char ph_replace_help[] =
    "Plays PHASE, a phase distortion synth, in place of one synth model in this project. The "
    "Instrument screen's Model list then shows PHASE where that model was. The list leaves "
    "out the model WAVE replaces.";

static const char synth_engines[] = "Synth Engines";

/* the Synth Engines folder's two option items' descriptors (the stock walk of Project
 * Settings: kind 1 folders, the label pointer ours), or 0 */
static struct menu_options *menu_desc(uint32_t item)
{
    const uint8_t *ps = (const uint8_t *)FW_PROJECT_SETTINGS;
    uint32_t n = ps[0x1D], cap = ps[0x08];
    const uint8_t *const *kids = *(const uint8_t *const *const *)(ps + 0x0C);
    if (ps[0] != 1 || !kids)
        return 0;
    for (uint32_t i = 0; i < n && i < cap; i++) {
        const uint8_t *f = kids[i];
        if (!f || f[0] != 1 || *(const char *const *)(f + 0x18) != synth_engines || f[0x1D] != 2 || f[0x08] != 2)
            continue;
        const uint8_t *const *items = *(const uint8_t *const *const *)(f + 0x0C);
        const uint8_t *it = items ? items[item] : 0;
        if (!it || it[0] != 2 || it[0x0C] != MENU_OPTIONS)
            return 0;
        return *(struct menu_options *const *)(it + 0x10);
    }
    return 0;
}

/* Both items' lists and positions from the choices: each item leaves out the other's
 * model. A const descriptor (the heap had no room) keeps the full list, and the two
 * choices then meet the conflict rule (menu_conflict). */
static void menu_refresh(void)
{
    volatile uint8_t *pos[2] = {&SY->menu_pos, &SY->ph_menu_pos};
    uint32_t choice[2] = {SY->menu, SY->ph_menu};
    for (uint32_t i = 0; i < 2; i++) {
        struct menu_options *d = menu_desc(i);
        uint32_t without = replace_of(choice[1 - i]);
        if (d && d->value == pos[i] && d != &replace_desc && d != &ph_replace_desc) {
            d->names = without < MODELS ? replace_without[without] : replace_names;
            d->count = without < MODELS ? MODELS : 1u + MODELS;
        } else {
            without = REPLACE_NONE;
        }
        *pos[i] = pos_of(choice[i], without);
    }
}

/* the menu's apply stored the position (cfg_menu_apply); the choice follows at once, and
 * the other item's list leaves it out. The Config screen's update applies it. */
static void option_applied(void)
{
    struct menu_options *d = menu_desc(0);
    SY->menu = menu_of(SY->menu_pos, d && d->value == &SY->menu_pos ? list_without(d) : REPLACE_NONE);
    menu_refresh();
}

static void ph_option_applied(void)
{
    struct menu_options *d = menu_desc(1);
    SY->ph_menu = menu_of(SY->ph_menu_pos, d && d->value == &SY->ph_menu_pos ? list_without(d) : REPLACE_NONE);
    menu_refresh();
}

/* a descriptor the menu refresh can change (from the heap), or the const one */
static const struct menu_options *heap_desc(const struct menu_options *proto)
{
    struct menu_options *d = (struct menu_options *)((malloc_fn)FW_MALLOC)(sizeof *d);
    if (!d)
        return proto;
    *d = *proto;
    return d;
}

/* replaces "bl 0x60118394" at 0x60118b00 (chained): Project Settings gets room for the
 * Synth Engines folder */
void *mt_syn_settings_folder(void *node, void *parent, const char *label, uint32_t capacity)
{
    return ((folder_fn)mt_syn_next_folder)(node, parent, label, capacity + 1);
}

/* replaces the constructor's last "bl 0x601183e8" (Licenses) at 0x601193e6 (chained):
 * a static constructor, so this runs at every start, before the synths exist */
void *mt_syn_last_item(void *node, void *parent, const char *label, const char *help,
                       uint32_t type, const void *descriptor)
{
    sy_audio_reset();
    void *r = ((item_fn)mt_syn_next_last_item)(node, parent, label, help, type, descriptor);
    sy_init_if_needed();
    malloc_fn alloc = (malloc_fn)FW_MALLOC;
    void *folder = alloc(MENU_FOLDER_SIZE);
    if (folder) {
        ((folder_fn)FW_MENU_FOLDER)(folder, (void *)FW_PROJECT_SETTINGS, synth_engines, 2);
        void *item = alloc(MENU_ITEM_SIZE);
        if (item)
            ((item_fn)FW_MENU_ITEM)(item, folder, "WAVE replaces", replace_help, MENU_OPTIONS,
                                    heap_desc(&replace_desc));
        item = alloc(MENU_ITEM_SIZE);
        if (item)
            ((item_fn)FW_MENU_ITEM)(item, folder, "PHASE replaces", ph_replace_help, MENU_OPTIONS,
                                    heap_desc(&ph_replace_desc));
    }
    menu_refresh();                         /* the positions valid before the menu ever draws */
    return r;
}

/* ---- synths.bin ------------------------------------------------------------------ */

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

#define FILE_MAGIC          0x32595348u     /* "HSY2" (sy1's "HSY1" files count as missing) */
#define FILE_SIZE           12u             /* magic, WAVE's and PHASE's menu values, 2 zero bytes,
                                             * a checksum (sy7 and before: PHASE's byte 0) */

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

static int file_manager_idle(void)
{
    const volatile uint8_t *fm = (const volatile uint8_t *)FW_FM;
    uint8_t state = fm[FM_STATE];
    return (state == 0 || state == 0x0C) && (fm[FM_SUBSTATE] & 0xFD) == 0;
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
    int n = ((snprintf_fn)FW_SNPRINTF)(path, size, "%s/%s/synths.bin",
                                       (const char *)FW_PROJECT_PATH, (const char *)FW_PROJECT_NAME);
    return n > 0 && (uint32_t)n < size;
}

/* 1: the file was read (the menu values and the saved ones set from it; PHASE Off if both
 * name one model) */
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
    ok = ok && get32(buf) == FILE_MAGIC && get32(buf + 8) == data_sum(buf + 4) && buf[4] <= MODELS
         && buf[5] <= MODELS && !buf[6] && !buf[7];
    if (!ok)
        return 0;
    SY->menu = SY->saved = buf[4];
    SY->ph_menu = SY->ph_saved = buf[4] && buf[5] == buf[4] ? 0 : buf[5];
    SY->saved_valid = 1;
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
    uint8_t value = SY->menu, ph_value = SY->ph_menu;
    put32(buf, FILE_MAGIC);
    buf[4] = value;
    buf[5] = ph_value;
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
        SY->saved = value;
        SY->ph_saved = ph_value;
        SY->saved_valid = 1;
    }
}

/* replaces "bl synth_slots_apply_project_q" at 0x60116382 (a project being loaded, at
 * start-up too; chained): the project's choice to the model table first, so the stock
 * code sets each slot's model, and then loads its patch, as the project had them. The
 * stock code is reading the project's files here, so one small read more is in place. */
void mt_syn_project_records(void *s, void *records, uint32_t reset)
{
    sy_init_if_needed();
    SY->session = project_session();
    SY->unnamed = (uint8_t)project_unsaved();
    SY->wait = SAVE_DELAY;
    if (!settings_load()) {
        SY->menu = SY->saved = 0;           /* no file: Off, and nothing to write */
        SY->ph_menu = SY->ph_saved = 0;
        SY->saved_valid = 1;
    }
    swap_table(replace_of(SY->menu), replace_of(SY->ph_menu));
    menu_refresh();
    SY->loads++;
    ((void (*)(void *, void *, uint32_t))mt_syn_next_records)(s, records, reset);
}

/* Called from the Pattern and Config screens' update methods (where the stock code
 * does its own file I/O): the choice saved a moment after it changes, once the file
 * manager is idle. A Save As keeps the choice and writes it to the new folder
 * (mt_syn_save_name); a project that is loaded went through mt_syn_project_records. */
static void settings_tick(void)
{
    sy_init_if_needed();
    if (!*(void **)FW_FM)
        return;
    uint32_t session = project_session();
    int unnamed = project_unsaved();
    if (SY->menu > MODELS)
        SY->menu = 0;
    if (SY->ph_menu > MODELS)
        SY->ph_menu = 0;
    if (session != SY->session) {
        SY->session = session;
        SY->unnamed = (uint8_t)unnamed;
        SY->wait = SAVE_DELAY;
        if (unnamed || !settings_load()) {  /* a file there wins; it is never overwritten unread */
            SY->saved = SY->ph_saved = 0;   /* a new project, a name without a file: the choices go with it; */
            SY->saved_valid = 1;            /* Off needs no file */
        }
        return;
    }
    if (unnamed || (SY->saved_valid && SY->menu == SY->saved && SY->ph_menu == SY->ph_saved)) {
        SY->wait = SAVE_DELAY;
        return;
    }
    if (SY->wait) {
        SY->wait--;
        return;
    }
    if (!file_manager_idle())               /* after a Save As: the new folder made first */
        return;
    SY->wait = SAVE_DELAY;
    settings_save();
}

/* Pattern screen vtable slot 0x600403dc (update method 0x600e1f99; chained): the unit
 * has booted, so the engine may run */
void mt_syn_pattern_update(void *screen, void *a1, void *a2, void *a3)
{
    settings_tick();
    SY->ready = SY_READY;
    apply_menu();
    menu_refresh();
    sweep();
    ((screen_update_fn)mt_syn_next_pattern_update)(screen, a1, a2, a3);
}

/* Config screen vtable slot 0x6003b5f8 (update method 0x600c6475; chained): the menu's
 * choice applies here */
void mt_syn_config_update(void *screen, void *a1, void *a2, void *a3)
{
    settings_tick();
    apply_menu();
    menu_refresh();
    sweep();
    ((screen_update_fn)mt_syn_next_config_update)(screen, a1, a2, a3);
}

/* 0x60114eaa in the save request 0x60114e34: "bl strncpy(fm + 0x340, name, 0x21)",
 * run only for Save As, after the new path is copied. The choices belong to the new
 * session at once and are marked unsaved, so the next tick doesn't read the folder's
 * file (an overwritten project's synths.bin) and writes them there instead. (Any
 * screen: the request comes from the project screen, whose update isn't hooked.) The
 * session field takes the mark, as synths' RAM block has no room for another. */
char *mt_syn_save_name(char *dst, const char *src, uint32_t n)
{
    char *r = ((strncpy_fn)mt_syn_next_save_name)(dst, src, n);
    sy_init_if_needed();
    SY->session = project_session();
    SY->unnamed = (uint8_t)project_unsaved();
    SY->saved_valid = 0;
    SY->wait = SAVE_DELAY;
    return r;
}

/* ---- where the state is, for the emulator tests (tools/emu/test_synths.py): a marker,
 * the state's address (relocated by the patcher with the rest), then its layout -------- */
const uint32_t mt_syn_ram_map[] = {
    0x50414D52u,                            /* "RMAP" */
    (uint32_t)&syn_state_ram,
    offsetof(struct syn_state, menu),
    offsetof(struct syn_state, applied),
    offsetof(struct syn_state, ready),
    offsetof(struct syn_state, switches),
    offsetof(struct syn_state, loads),
    offsetof(struct syn_state, model_ids),
    offsetof(struct syn_state, param),
    offsetof(struct syn_state, voice),
    sizeof(struct wt_voice),
    WT_PARAMS,
    sizeof(struct syn_state),
    (uint32_t)wt_data,                      /* the wavetables and their layout */
    WT_BANKS,
    WT_FRAMES,
    WT_FRAME_STRIDE,
    (uint32_t)wt_level_off,
    (uint32_t)wt_level_len,
    WT_LEVELS,
    (uint32_t)wave_builder,                 /* WAVE's builder (the descriptor's functor) */
    offsetof(struct syn_state, fx_ready),   /* the effect: its state in the late RAM block */
    sizeof(struct wt_fx),
    offsetof(struct wt_fx, p),
    offsetof(struct wt_fx, running),
    offsetof(struct syn_state, ph_menu),    /* PHASE (sy8) */
    offsetof(struct syn_state, ph_applied),
    (uint32_t)phase_builder,
    offsetof(struct syn_state, menu_pos),   /* the menu's positions in the two lists */
    offsetof(struct syn_state, ph_menu_pos),
};

/* ---- version text: Config shows 1.2.0-<SYNTHS_VERSION> -------------------------- */
const char mt_version_fmt[] = "%d.%d.%d-" SYNTHS_VERSION;
