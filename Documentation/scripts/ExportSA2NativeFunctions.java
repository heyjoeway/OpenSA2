// Ghidra headless postScript for this user's private Sonic Adventure 2 PC copy.
// Emits decompiled C to the headless script log. Keep that log in work/:
// decompiled game code is analysis material, not a distributable project asset.
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.SourceType;

public class ExportSA2NativeFunctions extends GhidraScript {
    private static final String[][] TARGETS = {
        {"0045B610", "SA2_PGetAcceleration"},
        {"0045F4D0", "SA2_PGetFriction"},
        {"0045FA70", "SA2_PGetRotation"},
        {"0045A870", "SA2_ReadMovementInput"},
        {"004603D0", "SA2_GroundTurn"},
        {"00446960", "SA2_ClampYawTowardTarget"},
        {"00460570", "SA2_TurnFromStop"},
        {"00460610", "SA2_FastTurn"},
        {"00460860", "SA2_PGetSpeed"},
        {"0045D770", "SA2_PGetAccelerationAir"},
        {"00460260", "SA2_PResetAngle"},
        {"0046E630", "SA2_JumpCollisionResponse"},
        {"004692A0", "SA2_PlayAnimationThing"},
        {"00474F80", "SA2_SonicGaitSelector"},
        {"00717840", "SA2_SonicMain"},
        {"00719920", "SA2_SonicInputAndDamageTransitions"},
        {"00720E00", "SA2_SonicAcquireHomingTarget"},
        {"00720F00", "SA2_SonicApplyHomingFlight"},
        {"007220E0", "SA2_SonicCheckNextAction"},
        {"007230E0", "SA2_SonicCheckActionWindow"}
    };

    @Override
    public void run() throws Exception {
        DecompInterface decompiler = new DecompInterface();
        if (!decompiler.openProgram(currentProgram)) {
            throw new IllegalStateException("Ghidra decompiler could not open the SA2 PE program.");
        }
        try {
            println("SA2_NATIVE_EXPORT program=" + currentProgram.getName());
            for (String[] target : TARGETS) {
                Address entry = toAddr(target[0]);
                Function function = getFunctionAt(entry);
                if (function == null) {
                    disassemble(entry);
                    function = createFunction(entry, target[1]);
                }
                if (function == null || !function.getEntryPoint().equals(entry)) {
                    printerr("SA2_NATIVE_MISSING entry=0x" + target[0]);
                    continue;
                }
                function.setName(target[1], SourceType.USER_DEFINED);
                DecompileResults result = decompiler.decompileFunction(function, 180, monitor);
                if (!result.decompileCompleted() || result.getDecompiledFunction() == null) {
                    printerr("SA2_NATIVE_DECOMPILE_FAILED entry=0x" + target[0] +
                        " error=" + result.getErrorMessage());
                    continue;
                }
                println("SA2_NATIVE_BEGIN entry=0x" + target[0] + " name=" + target[1]);
                println(result.getDecompiledFunction().getC());
                println("SA2_NATIVE_END entry=0x" + target[0]);
            }
        } finally {
            decompiler.dispose();
        }
    }
}
