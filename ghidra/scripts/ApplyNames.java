// Apply function names (and plate comments) from a TSV: address<TAB>name[<TAB>note].
// Lines starting with # are ignored. Creates the function if needed.
// Usage (headless): -postScript ApplyNames.java <names.tsv>
// @category TrackerPlus
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.CodeUnit;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.SourceType;

import java.nio.file.Files;
import java.nio.file.Paths;

public class ApplyNames extends GhidraScript {
    @Override
    public void run() throws Exception {
        int n = 0;
        for (String line : Files.readAllLines(Paths.get(getScriptArgs()[0]))) {
            if (line.isBlank() || line.startsWith("#")) continue;
            String[] f = line.split("\t");
            Address at = toAddr(Long.parseLong(f[0], 16) & ~1L);
            Function fn = getFunctionAt(at);
            if (fn == null) {
                disassemble(at);
                fn = createFunction(at, f[1]);
            }
            if (fn == null) { println("could not create function at " + at); continue; }
            fn.setName(f[1], SourceType.USER_DEFINED);
            if (f.length > 2) setPlateComment(at, f[2]);
            n++;
        }
        println("named " + n + " functions");
    }
}
