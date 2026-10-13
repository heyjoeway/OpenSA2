// sa2_dialog.h: the launcher's dialogs for a person at the desktop: a message box with buttons, and
// a folder or file picker. They are SDL3's (SDL_ShowMessageBox, SDL_ShowFileDialogWithProperties),
// from the release's own SDL3 loaded at run time (lib/libSDL3.so.0 beside bin/ on Linux,
// bin/SDL3.dll on Windows: sdl_library), so the launcher links no SDL and still runs where there
// is none (the selftest, a test's release, a terminal with no display). The launcher shows them in
// play alone (sa2_launch.cpp): the first run's folder question, and an error a person would
// otherwise never see (a launch from a file manager, or from Explorer with its console hidden).
//
// On Linux SDL shows a message box through zenity, or X11's own when zenity fails, and a picker
// through the desktop portal (D-Bus) or zenity; with neither, or with no display at all (DISPLAY
// and WAYLAND_DISPLAY unset, which `usable` checks before SDL is asked), no dialog can be shown and
// the caller says what to run instead. On Windows they are the system's own dialogs.
#pragma once

#include <string>
#include <utility>
#include <vector>

namespace sa2d {

enum class Kind { Info, Warning, Error };
enum class Pick { Chosen, Cancelled, Failed };

// The release's SDL3 for a launcher in `bin_dir`.
std::string sdl_library(const std::string& bin_dir);

class Dialogs {
public:
    explicit Dialogs(std::string library) : library_(std::move(library)) {}
    ~Dialogs();
    Dialogs(const Dialogs&) = delete;
    Dialogs& operator=(const Dialogs&) = delete;

    // Whether a dialog can be asked for at all, and why not: the library is absent or lacks a
    // function, or (Linux) there is no display. SDL itself may still fail to show one (ask, pick).
    bool usable(std::string* why);
    // A message box with `buttons`, left to right: the index of the one chosen; -1 when the box
    // was closed without one (or the escape button's index, when given); -2 when no box could be
    // shown, with *why.
    int ask(Kind kind, const std::string& title, const std::string& text, const std::vector<std::string>& buttons,
            int enter_button, int escape_button, std::string* why);
    // A message box with one OK button. False when none could be shown, with *why.
    bool tell(Kind kind, const std::string& title, const std::string& text, std::string* why);
    // A folder picker (`folder`), or a file picker offering the files `filter_pattern` matches
    // ("zip": SDL's pattern, extensions without the dot) under `filter_name`; its window titled
    // `title`. Chosen with the path in `chosen`; Cancelled when the person closed it; Failed when
    // no picker could be shown, with *why (SDL's error).
    Pick pick(bool folder, const std::string& title, const std::string& filter_name, const std::string& filter_pattern,
              std::string& chosen, std::string* why);
    // Ends what SDL started (its events subsystem); the library stays loaded.
    void close();
    const std::string& library() const { return library_; }

private:
    struct Api;
    bool load(std::string* why);
    std::string library_, load_error_;
    Api* api_ = nullptr;
    bool tried_ = false;
};

}  // namespace sa2d
