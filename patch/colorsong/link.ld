/* Linked right after the stock 1.2.0 image (ends 0x601bab78), where colorsong lands
 * alone or first; tools/patch_ptf.py moves it elsewhere with build/colorsong.rel. */
MEMORY
{
    PATCH (rx) : ORIGIN = 0x601BAB80, LENGTH = 16K
}

SECTIONS
{
    .text : {
        KEEP(*(.text.mt_*))
        *(.text*)
        *(.rodata*)
    } > PATCH

    /* No initialised or zeroed RAM: the patch keeps its state at fixed addresses. */
    .data : { *(.data*) } > PATCH
    .bss  : { *(.bss*) *(COMMON) } > PATCH
    ASSERT(SIZEOF(.data) == 0, "colorsong must not use .data")
    ASSERT(SIZEOF(.bss) == 0, "colorsong must not use .bss")

    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.attributes) *(.comment) }
}
