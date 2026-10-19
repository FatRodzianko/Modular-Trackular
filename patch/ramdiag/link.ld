/* Link address only: tools/patch_ptf.py places the code after the stock image and moves
 * it there with build/ramdiag.rel. */
MEMORY
{
    PATCH (rx) : ORIGIN = 0x601BD000, LENGTH = 4K
    RAM (rw)   : ORIGIN = 0x20205000, LENGTH = 64K     /* link address only: the patcher assigns RAM */
}

SECTIONS
{
    .text : {
        KEEP(*(.text.mt_*))
        *(.text*)
        *(.rodata*)
    } > PATCH

    /* No initialised or zeroed RAM: state goes in .mt_ram (below). */
    .data : { *(.data*) } > PATCH
    .bss  : { *(.bss*) *(COMMON) } > PATCH
    ASSERT(SIZEOF(.data) == 0, "ramdiag must not use .data")
    ASSERT(SIZEOF(.bss) == 0, "ramdiag must not use .bss")

    /* The patch's RAM: its state variables (section ".mt_ram"), neither loaded nor
     * zeroed. Linked at a placeholder address; tools/patch_ptf.py assigns it from the
     * free RAM regions and moves the references with build/<name>.rel. */
    .mt_ram (NOLOAD) : ALIGN(16) {
        __mt_ram_start = .;
        *(.mt_ram*)
        . = ALIGN(16);
        __mt_ram_end = .;
    } > RAM

    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.attributes) *(.comment) }
}
