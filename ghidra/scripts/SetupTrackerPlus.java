// Pre-analysis setup for the Tracker+ 1.2.0 image imported as a raw binary at 0x60030000.
// Creates the i.MX RT105x/106x memory map (as configured by the firmware's own startup code),
// copies the startup "data" sections into their RAM addresses, labels the vector table
// and peripherals, and turns on the analyzers that help with raw Thumb code.
//
// Memory facts are from the 1.2.0 reset handler (0x60030318):
//   FlexRAM GPR17 = 0xaaaaabfd -> 128 KB ITCM, 352 KB DTCM, 32 KB OCRAM
//   data copy table 0x600302a0, bss table 0x600302d0
// @category TrackerPlus
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.SourceType;
import ghidra.program.model.listing.Function;

public class SetupTrackerPlus extends GhidraScript {

    static final long FLASH = 0x60030000L;

    Address a(long off) { return toAddr(off); }

    long rd32(long addr) throws Exception {
        return getInt(a(addr)) & 0xffffffffL;
    }

    MemoryBlock block(String name, long start, long size, boolean init, boolean volatil, boolean exec)
            throws Exception {
        Memory mem = currentProgram.getMemory();
        MemoryBlock b = init
            ? mem.createInitializedBlock(name, a(start), size, (byte) 0, monitor, false)
            : mem.createUninitializedBlock(name, a(start), size, false);
        b.setRead(true);
        b.setWrite(!exec || name.equals("ITCM"));
        b.setExecute(exec);
        b.setVolatile(volatil);
        return b;
    }

    void copyIn(long load, long exec, long len) throws Exception {
        if (len == 0) return;
        byte[] buf = getBytes(a(load), (int) len);
        currentProgram.getMemory().setBytes(a(exec), buf);
        println(String.format("copied %#x bytes %#010x -> %#010x", len, load, exec));
    }

    void label(long addr, String name) throws Exception {
        createLabel(a(addr), name, true, SourceType.USER_DEFINED);
    }

    void func(long addr, String name) throws Exception {
        Address at = a(addr & ~1L);
        disassemble(at);
        Function f = getFunctionAt(at);
        if (f == null) f = createFunction(at, name);
        if (f != null && name != null) f.setName(name, SourceType.USER_DEFINED);
    }

    @Override
    public void run() throws Exception {
        currentProgram.getMemory().getBlock(a(FLASH)).setName("FLEXSPI");
        currentProgram.getMemory().getBlock(a(FLASH)).setWrite(false);
        currentProgram.getMemory().getBlock(a(FLASH)).setExecute(true);

        // RAM. ITCM starts at 0 but the firmware never uses the first 8 bytes.
        block("ITCM", 0x00000000L, 0x20000L, true, false, true);
        block("DTCM", 0x20000000L, 0x58000L, true, false, false);
        // On-chip OCRAM is the single FlexRAM bank set up as OCRAM: 32 KB at 0x20200000.
        // Nothing else exists there on the Tracker+ (an access to 0x20280000 froze the unit).
        block("OCRAM", 0x20200000L, 0x8000L, false, false, false);
        block("SDRAM", 0x80000000L, 0x4000000L, false, false, false);
        block("PERIPH", 0x40000000L, 0x4000000L, false, true, false); // includes GPIO6-9 at 0x42000000
        block("PPB", 0xE0000000L, 0x100000L, false, true, false);

        // Startup data-copy table: {load, exec, length} triples.
        for (long t = 0x600302a0L; t < 0x600302d0L; t += 12) {
            copyIn(rd32(t), rd32(t + 4), rd32(t + 8));
        }

        // Vector table.
        String[] names = { "initial_sp", "Reset_Handler", "NMI_Handler", "HardFault_Handler",
            "MemManage_Handler", "BusFault_Handler", "UsageFault_Handler", null, null, null, null,
            "SVC_Handler", "DebugMon_Handler", null, "PendSV_Handler", "SysTick_Handler" };
        for (int i = 0; i < 0x2a0 / 4; i++) { // vector table ends at the data table
            long v = rd32(FLASH + 4L * i);
            createDWord(a(FLASH + 4L * i));
            if (i == 0 || v == 0) continue;
            String n = i < 16 ? names[i] : String.format("IRQ%d_Handler", i - 16);
            if (n == null) continue;
            if ((v & 1) == 1 && (v >= FLASH || v < 0x20000)) {
                try { func(v, n); } catch (Exception e) { println("vector " + i + ": " + e); }
            }
        }
        label(FLASH, "vector_table");
        label(0x600302a0L, "data_section_table");
        label(0x600302d0L, "bss_section_table");
        func(0x60094b51L, "SystemInit");
        func(0x60166295L, "libc_init_array");
        func(0x600a1eddL, "main");

        // i.MX RT10xx peripheral base addresses (reference manual memory map).
        Object[][] periph = {
            {0x400AC000L, "IOMUXC_GPR"}, {0x401F8000L, "IOMUXC"}, {0x400FC000L, "CCM"},
            {0x400D8000L, "CCM_ANALOG"}, {0x400F8000L, "SRC"}, {0x400D4000L, "SNVS"},
            {0x400B8000L, "WDOG1"}, {0x400D0000L, "WDOG2"}, {0x400BC000L, "RTWDOG"},
            {0x402A8000L, "FLEXSPI"}, {0x402A4000L, "FLEXSPI2"}, {0x402F0000L, "SEMC"},
            {0x402C0000L, "USDHC1"}, {0x402C4000L, "USDHC2"},
            {0x402B8000L, "LCDIF"}, {0x402B4000L, "PXP"}, {0x402BC000L, "CSI"},
            {0x402E0000L, "USB1"}, {0x402E0200L, "USB2"}, {0x400D9000L, "USBPHY1"}, {0x400DA000L, "USBPHY2"},
            {0x400E8000L, "DMA0"}, {0x400EC000L, "DMAMUX"},
            {0x40384000L, "SAI1"}, {0x40388000L, "SAI2"}, {0x4038C000L, "SAI3"},
            {0x40394000L, "LPSPI1"}, {0x40398000L, "LPSPI2"}, {0x4039C000L, "LPSPI3"}, {0x403A0000L, "LPSPI4"},
            {0x403F0000L, "LPI2C1"}, {0x403F4000L, "LPI2C2"}, {0x403F8000L, "LPI2C3"}, {0x403FC000L, "LPI2C4"},
            {0x40184000L, "LPUART1"}, {0x40188000L, "LPUART2"}, {0x4018C000L, "LPUART3"}, {0x40190000L, "LPUART4"},
            {0x40194000L, "LPUART5"}, {0x40198000L, "LPUART6"}, {0x4019C000L, "LPUART7"}, {0x401A0000L, "LPUART8"},
            {0x401B8000L, "GPIO1"}, {0x401BC000L, "GPIO2"}, {0x401C0000L, "GPIO3"}, {0x401C4000L, "GPIO4"},
            {0x400C0000L, "GPIO5"}, {0x42000000L, "GPIO6"}, {0x42004000L, "GPIO7"}, {0x42008000L, "GPIO8"},
            {0x4200C000L, "GPIO9"}, {0x40084000L, "PIT"}, {0x401EC000L, "GPT1"}, {0x401F0000L, "GPT2"},
            {0x400C4000L, "ADC1"}, {0x400C8000L, "ADC2"}, {0x401AC000L, "FLEXIO1"}, {0x401B0000L, "FLEXIO2"},
            {0x42020000L, "FLEXIO3"}, {0x401FC000L, "KPP"}, {0x401F4000L, "OCOTP"}, {0x402D8000L, "ENET"},
            {0x403E8000L, "FLEXPWM1"}, {0x403DC000L, "FLEXPWM2"}, {0x403E0000L, "FLEXPWM3"}, {0x403E4000L, "FLEXPWM4"},
            {0xE000E000L, "SCS"}, {0xE000ED00L, "SCB"}, {0xE000E100L, "NVIC"}, {0xE000E010L, "SysTick"},
        };
        for (Object[] p : periph) label((Long) p[0], (String) p[1]);

        setAnalysisOption(currentProgram, "ARM Aggressive Instruction Finder", "true");
        setAnalysisOption(currentProgram, "Non-Returning Functions - Discovered", "true");
        println("TrackerPlus memory map ready");
    }
}
