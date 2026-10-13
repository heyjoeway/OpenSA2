// Ghidra headless postScript for the user's private Sonic Adventure 2 PC copy.
// Emits camera decompilation only to an ignored work/ log. Do not distribute
// that generated proprietary-code output with the Unity project.
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.SourceType;

public class ExportSA2CameraFunctions extends GhidraScript {
    private static final String[][] TARGETS = {
        {"004EB650", "SA2_CameraSetGlobalLocation"},
        {"004EB8E0", "SA2_CameraMain"},
        {"004EE490", "SA2_CameraPrepareModes"},
        {"004EC520", "SA2_RunCameraTarget"},
        {"004EC770", "SA2_CameraUpdateLocation"},
        {"004EC840", "SA2_CameraApplyPostAdjust"},
        {"004EC920", "SA2_CameraUpdateFOV"},
        {"004EC9A0", "SA2_RunCameraCollision"},
        {"004ECC30", "SA2_CameraCommitView"},
        {"004ECD70", "SA2_CameraGetPlayerOffset"},
        {"004EFDE0", "SA2_CameraCollisionResolve"},
        {"004F0680", "SA2_CameraPreparePlayer"},
        {"004F0730", "SA2_RunCameraModes"},
        {"004F07C0", "SA2_CameraDirectionToAngles"},
        {"004F0900", "SA2_CameraAnglesToDirection"},
        {"004F0BC0", "SA2_CameraCollisionPrepare"},
        {"004F1D00", "SA2_CameraAdjustAngles"},
        {"004F1D60", "SA2_CameraAdjustPosition"},
        {"004F1DC0", "SA2_CameraAdjustTarget"},
        {"004F1F50", "SA2_CameraAdjustDirection"},
        {"004F1FB0", "SA2_CameraBuildAdjustment"},
        {"004F1970", "SA2_CameraAdjustInterpolate"},
        {"004F16C0", "SA2_CameraModeEasyset"},
        {"004F2550", "SA2_CameraCarmelFinalize"},
        {"004F2620", "SA2_CameraCarmelFallback"},
        {"004F2690", "SA2_CameraCarmelCandidateDistance"},
        {"004F2990", "SA2_CameraCarmelUsePlayerTarget"},
        {"004F2B10", "SA2_CameraModeCarmel"},
        {"004F3D30", "SA2_CameraKnucklesInitialOffset"},
        {"004F3DB0", "SA2_CameraKnucklesSpecial"},
        {"004F4790", "SA2_CameraKnucklesSpecialCheck"},
        {"004F47F0", "SA2_CameraModeKnuckles"},
        {"004F53D0", "SA2_CameraModeFix"},
        {"004F5BA0", "SA2_CameraFindVolume"},
        {"004F5C90", "SA2_CameraValidateVolume"},
        {"004F5D80", "SA2_CameraFindSplinePoint"},
        {"004F5E30", "SA2_CameraEvaluateSpline"},
        {"004F5620", "SA2_CameraAdjustHalf"},
        {"004F5760", "SA2_CameraModeFollow"}
    };

    @Override
    public void run() throws Exception {
        DecompInterface decompiler = new DecompInterface();
        if (!decompiler.openProgram(currentProgram)) {
            throw new IllegalStateException("Ghidra decompiler could not open the SA2 PE program.");
        }
        try {
            println("SA2_CAMERA_EXPORT program=" + currentProgram.getName());
            for (String[] target : TARGETS) {
                Address entry = toAddr(target[0]);
                Function function = getFunctionAt(entry);
                if (function == null) {
                    disassemble(entry);
                    function = createFunction(entry, target[1]);
                }
                if (function == null || !function.getEntryPoint().equals(entry)) {
                    printerr("SA2_CAMERA_MISSING entry=0x" + target[0]);
                    continue;
                }
                function.setName(target[1], SourceType.USER_DEFINED);
                DecompileResults result = decompiler.decompileFunction(function, 180, monitor);
                if (!result.decompileCompleted() || result.getDecompiledFunction() == null) {
                    printerr("SA2_CAMERA_DECOMPILE_FAILED entry=0x" + target[0] +
                        " error=" + result.getErrorMessage());
                    continue;
                }
                println("SA2_CAMERA_BEGIN entry=0x" + target[0] + " name=" + target[1]);
                println(result.getDecompiledFunction().getC());
                println("SA2_CAMERA_END entry=0x" + target[0]);
            }
        } finally {
            decompiler.dispose();
        }
    }
}
