# Contribute to OpenSA2

OpenSA2 is an experimental source preview. Contributions are useful when they make a failure reproducible, improve the build setup, or add a checked implementation of an unfinished path.

Read the [current status](docs/status.md) and [architecture](docs/architecture.md) before changing code. The [getting-started guide](docs/getting-started.md) has source-only checks for the installer and launcher.

## Report a bug

Use the bug-report form and include:

- Your platform, CPU, GPU, and driver, or your Android device and version.
- The public revision or locally built version you tested.
- The game mode, stage or event, and the steps that reproduce the failure.
- What you expected and what happened.
- Relevant text output, including a missing-block address if the runtime reports one.

Remove personal paths and identifiers from logs before posting them. Do not upload the game executable, game assets, translated modules, or captures that contain game data. A short text error and reproduction steps are a useful starting point.

## Prepare a change

1. Keep the change focused on one behavior or one build problem.
2. Use the existing types, functions, and stream contracts.
3. For a behavior change, reproduce the problem and add a check for the observed result when a suitable test exists.
4. Run `python3 tools/public_check.py` and the checks that cover the changed code.
5. Describe the problem, the resulting behavior, and the validation in the pull request.

Synthetic tests are preferred when they can prove the behavior without game data. If a change requires the original executable, keep those inputs and generated outputs local. Explain the tested path and the limits of the result in the pull request.

A runtime, translation, or renderer change needs evidence about the behavior it changes. Compilation alone does not establish gameplay fidelity. The project compares translated state, recorded calls, or rendered output where the relevant tools support it.

## Keep the repository source-only

Do not commit:

- Game executables, DLLs, assets, or save files.
- Generated code derived from the game or compiled game binaries.
- Game recordings, screenshots, fixtures, or streams that contain shipped data.
- Local build outputs, device identifiers, or private test records.

Small synthetic fixtures and original test assets are suitable when their origin is clear. Keep dependencies' copyright and license notices intact. Do not copy code from another project without preserving the required attribution and license terms.

## Improve the docs

Use plain language and real command names. State the prerequisite before a command. Describe experimental behavior as experimental, and distinguish a tested path from general compatibility.

If you change a public command, file format, platform requirement, or known limitation, update the corresponding guide in the same pull request.

Original project code is licensed under [GPL-3.0-only](LICENSE). Contributions to that code use the same license. Third-party dependencies retain their own licenses. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
