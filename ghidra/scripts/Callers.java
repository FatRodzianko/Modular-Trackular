// Print the callers of each given function (and their callers, to a given depth).
// Usage (headless): -postScript Callers.java <outfile> <depth> <addr> [<addr> ...]
// Output lines: "<addr> <- caller1@entry caller2@entry ..." indented per level.
// @category TrackerPlus
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;

import java.io.File;
import java.io.PrintWriter;
import java.util.Set;

public class Callers extends GhidraScript {
    PrintWriter w;

    void walk(Function f, int depth, String indent) throws Exception {
        Set<Function> callers = f.getCallingFunctions(monitor);
        StringBuilder sb = new StringBuilder();
        for (Function c : callers) sb.append(" ").append(c.getName()).append("@").append(c.getEntryPoint());
        w.println(indent + f.getName() + "@" + f.getEntryPoint() + " <-" + sb);
        if (depth > 1) for (Function c : callers) walk(c, depth - 1, indent + "  ");
    }

    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        int depth = Integer.parseInt(a[1]);
        try (PrintWriter pw = new PrintWriter(new File(a[0]), "UTF-8")) {
            w = pw;
            for (int i = 2; i < a.length; i++) {
                Function f = getFunctionContaining(toAddr(a[i]));
                if (f == null) { w.println(a[i] + " <- (no function)"); continue; }
                walk(f, depth, "");
            }
        }
        println("wrote " + a[0]);
    }
}
