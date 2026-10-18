// Create functions at a list of Thumb addresses (one hex address per line, low bit ignored),
// e.g. every entry of the vtables found by tools/vtables.py, then let analysis catch up.
// Usage (headless): -postScript CreateFunctions.java <addresses.txt>
// @category TrackerPlus
import ghidra.app.cmd.disassemble.ArmDisassembleCommand;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

import java.nio.file.Files;
import java.nio.file.Paths;

public class CreateFunctions extends GhidraScript {
    @Override
    public void run() throws Exception {
        int made = 0, had = 0, failed = 0;
        for (String line : Files.readAllLines(Paths.get(getScriptArgs()[0]))) {
            line = line.trim();
            if (line.isEmpty() || line.startsWith("#")) continue;
            Address at = toAddr(Long.decode(line) & ~1L);
            if (getFunctionAt(at) != null) { had++; continue; }
            if (getInstructionAt(at) == null) {
                clearListing(at);
                new ArmDisassembleCommand(at, null, true).applyTo(currentProgram, monitor);
            }
            Function f = createFunction(at, null);
            if (f != null) made++; else failed++;
        }
        analyzeChanges(currentProgram);
        println("functions: created " + made + ", already existed " + had + ", failed " + failed);
    }
}
