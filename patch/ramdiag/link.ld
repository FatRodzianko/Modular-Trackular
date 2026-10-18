/* Link address only: tools/patch_ptf.py places the code after the stock image and moves
 * it there with build/ramdiag.rel. */
MEMORY
{
    PATCH (rx) : ORIGIN = 0x601BD000, LENGTH = 4K
}

SECTIONS
{
    .text : {
        KEEP(*(.text.mt_*))
        *(.text*)
        *(.rodata*)
    } > PATCH

    /* No initialised or zeroed RAM: ramdiag keeps its state at a fixed OCRAM address. */
    .data : { *(.data*) } > PATCH
    .bss  : { *(.bss*) *(COMMON) } > PATCH
    ASSERT(SIZEOF(.data) == 0, "ramdiag must not use .data")
    ASSERT(SIZEOF(.bss) == 0, "ramdiag must not use .bss")

    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.attributes) *(.comment) }
}
