// Decompile functions to C, one file per function.
// Usage (headless): -postScript Decompile.java <outdir> <addr> [<addr> ...]
// Each address may be any address inside the function. Output: <outdir>/<entry>_<name>.c,
// prefixed with the callers and callees so the call graph can be walked from text.
// @category TrackerPlus
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;

import java.io.File;
import java.io.PrintWriter;

public class Decompile extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        File out = new File(args[0]);
        out.mkdirs();
        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        for (int i = 1; i < args.length; i++) {
            Function f = getFunctionContaining(toAddr(args[i]));
            if (f == null) {
                disassemble(toAddr(Long.decode(args[i]) & ~1L));
                f = createFunction(toAddr(Long.decode(args[i]) & ~1L), null);
            }
            if (f == null) { println("no function at " + args[i]); continue; }
            DecompileResults r = di.decompileFunction(f, 120, monitor);
            File file = new File(out, f.getEntryPoint() + "_" + f.getName() + ".c");
            try (PrintWriter w = new PrintWriter(file, "UTF-8")) {
                StringBuilder callers = new StringBuilder();
                for (Function c : f.getCallingFunctions(monitor)) callers.append(" ").append(c.getName()).append("@").append(c.getEntryPoint());
                StringBuilder callees = new StringBuilder();
                for (Function c : f.getCalledFunctions(monitor)) callees.append(" ").append(c.getName()).append("@").append(c.getEntryPoint());
                w.println("// callers:" + callers);
                w.println("// callees:" + callees);
                w.println(r.decompileCompleted() ? r.getDecompiledFunction().getC() : "// decompile failed: " + r.getErrorMessage());
            }
            println("wrote " + file.getName());
        }
        di.dispose();
    }
}
