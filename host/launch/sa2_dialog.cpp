// sa2_dialog.cpp: sa2_dialog.h, on SDL3 loaded at run time (dlopen, or LoadLibraryW on Windows).
//
// The few SDL3 types and constants the launcher passes are declared here as SDL_messagebox.h,
// SDL_dialog.h, SDL_properties.h, SDL_init.h and SDL_hints.h define them (SDL 3.4.16, the pinned
// release): SDL3 keeps its ABI within the major version, and declaring them keeps the launcher's
// build free of SDL's headers.
#include "sa2_dialog.h"

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <mutex>
#include <thread>
#include <type_traits>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#else
#include <dlfcn.h>
#endif

namespace sa2d {

namespace {

// SDL_messagebox.h
struct MbButton {
    uint32_t flags;               // SDL_MessageBoxButtonFlags
    int buttonID;
    const char* text;
};
struct MbColor {
    uint8_t r, g, b;
};
struct MbScheme {
    MbColor colors[5];            // SDL_MESSAGEBOX_COLOR_COUNT
};
struct MbData {
    uint32_t flags;               // SDL_MessageBoxFlags
    void* window;                 // SDL_Window*
    const char* title;
    const char* message;
    int numbuttons;
    const MbButton* buttons;
    const MbScheme* colorScheme;
};
constexpr uint32_t kMbError = 0x10, kMbWarning = 0x20, kMbInformation = 0x40, kMbLeftToRight = 0x80;
constexpr uint32_t kButtonReturn = 0x1, kButtonEscape = 0x2;
// SDL_dialog.h
struct Filter {
    const char* name;
    const char* pattern;
};
using FileCallback = void (*)(void* userdata, const char* const* filelist, int filter);
constexpr int kOpenFile = 0, kOpenFolder = 2;   // SDL_FILEDIALOG_OPENFILE, SDL_FILEDIALOG_OPENFOLDER
// SDL_init.h, SDL_hints.h
constexpr uint32_t kInitEvents = 0x00004000u;

uint32_t kind_flags(Kind k) {
    return (k == Kind::Error ? kMbError : k == Kind::Warning ? kMbWarning : kMbInformation) | kMbLeftToRight;
}

#ifdef _WIN32
std::wstring wide(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), nullptr, 0);
    std::wstring w(size_t(n), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), &w[0], n);
    return w;
}
#endif

}  // namespace

struct Dialogs::Api {
    void* handle = nullptr;
    bool inited = false;
    bool (*Init)(uint32_t) = nullptr;
    void (*Quit)() = nullptr;
    const char* (*GetError)() = nullptr;
    bool (*SetHint)(const char*, const char*) = nullptr;
    bool (*ShowMessageBox)(const MbData*, int*) = nullptr;
    void (*ShowFileDialogWithProperties)(int, FileCallback, void*, uint32_t) = nullptr;
    uint32_t (*CreateProperties)() = nullptr;
    bool (*SetStringProperty)(uint32_t, const char*, const char*) = nullptr;
    bool (*SetPointerProperty)(uint32_t, const char*, void*) = nullptr;
    bool (*SetNumberProperty)(uint32_t, const char*, int64_t) = nullptr;
    void (*DestroyProperties)(uint32_t) = nullptr;
    void (*PumpEvents)() = nullptr;

    std::string error() const {
        const char* e = GetError ? GetError() : nullptr;
        return e && *e ? std::string(e) : std::string("SDL gave no reason");
    }
};

std::string sdl_library(const std::string& bin_dir) {
#ifdef _WIN32
    return bin_dir + "/SDL3.dll";
#else
    const size_t cut = bin_dir.find_last_of('/');
    return (cut == std::string::npos ? std::string(".") : bin_dir.substr(0, cut)) + "/lib/libSDL3.so.0";
#endif
}

Dialogs::~Dialogs() {
    close();
    delete api_;      // the library stays mapped: a thread SDL started may still be leaving it
}

void Dialogs::close() {
    if (api_ && api_->inited) {
        api_->Quit();
        api_->inited = false;
    }
}

bool Dialogs::load(std::string* why) {
    if (tried_) {
        if (!api_ && why) *why = load_error_;
        return api_ != nullptr;
    }
    tried_ = true;
#ifdef _WIN32
    // LoadLibrary takes backslashes alone; the folder's own copy, its dependencies from there too.
    std::string native = library_;
    for (char& c : native)
        if (c == '/') c = '\\';
    HMODULE h = LoadLibraryExW(wide(native).c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!h) {
        load_error_ = "cannot load " + library_ + " (Windows error " + std::to_string(GetLastError()) + ")";
        if (why) *why = load_error_;
        return false;
    }
    auto sym = [&](const char* name) { return reinterpret_cast<void*>(GetProcAddress(h, name)); };
#else
    void* h = dlopen(library_.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (!h) {
        const char* e = dlerror();
        load_error_ = "cannot load " + library_ + (e ? std::string(": ") + e : std::string());
        if (why) *why = load_error_;
        return false;
    }
    auto sym = [&](const char* name) { return dlsym(h, name); };
#endif
    Api* a = new Api;
    a->handle = reinterpret_cast<void*>(h);
    std::string missing;
    auto get = [&](auto& fn, const char* name) {
        void* p = sym(name);
        if (!p) missing += std::string(missing.empty() ? "" : ", ") + name;
        fn = reinterpret_cast<std::remove_reference_t<decltype(fn)>>(p);
    };
    get(a->Init, "SDL_Init");
    get(a->Quit, "SDL_Quit");
    get(a->GetError, "SDL_GetError");
    get(a->SetHint, "SDL_SetHint");
    get(a->ShowMessageBox, "SDL_ShowMessageBox");
    get(a->ShowFileDialogWithProperties, "SDL_ShowFileDialogWithProperties");
    get(a->CreateProperties, "SDL_CreateProperties");
    get(a->SetStringProperty, "SDL_SetStringProperty");
    get(a->SetPointerProperty, "SDL_SetPointerProperty");
    get(a->SetNumberProperty, "SDL_SetNumberProperty");
    get(a->DestroyProperties, "SDL_DestroyProperties");
    get(a->PumpEvents, "SDL_PumpEvents");
    if (!missing.empty()) {
        load_error_ = library_ + " lacks " + missing;
        if (why) *why = load_error_;
        delete a;
        return false;
    }
    // The launcher keeps its own SIGINT and SIGTERM (sa2l::catch_interrupts).
    a->SetHint("SDL_NO_SIGNAL_HANDLERS", "1");
    // The events subsystem pumps the desktop portal's replies (D-Bus); a host where it cannot
    // start may still show a box or a zenity picker, which need no pump.
    a->inited = a->Init(kInitEvents);
    api_ = a;
    return true;
}

bool Dialogs::usable(std::string* why) {
#ifndef _WIN32
    const char* x = std::getenv("DISPLAY");
    const char* w = std::getenv("WAYLAND_DISPLAY");
    if ((!x || !*x) && (!w || !*w)) {
        if (why) *why = "there is no display (DISPLAY and WAYLAND_DISPLAY are unset)";
        return false;
    }
#endif
    return load(why);
}

int Dialogs::ask(Kind kind, const std::string& title, const std::string& text, const std::vector<std::string>& buttons,
                 int enter_button, int escape_button, std::string* why) {
    if (!usable(why)) return -2;
    std::vector<MbButton> b;
    for (size_t i = 0; i < buttons.size(); i++) {
        uint32_t f = 0;
        if (int(i) == enter_button) f |= kButtonReturn;
        if (int(i) == escape_button) f |= kButtonEscape;
        b.push_back({f, int(i), buttons[i].c_str()});
    }
    MbData d{kind_flags(kind), nullptr, title.c_str(), text.c_str(), int(b.size()), b.data(), nullptr};
    int chosen = -1;
    if (!api_->ShowMessageBox(&d, &chosen)) {
        if (why) *why = api_->error();
        return -2;
    }
    if (chosen < 0 || chosen >= int(buttons.size())) return escape_button >= 0 ? escape_button : -1;
    return chosen;
}

bool Dialogs::tell(Kind kind, const std::string& title, const std::string& text, std::string* why) {
    return ask(kind, title, text, {"OK"}, 0, 0, why) >= 0;
}

namespace {

struct PickState {
    std::mutex mu;
    bool done = false, failed = false;
    std::string chosen, error;
    const char* (*get_error)() = nullptr;
};

// SDL calls it once, from this thread or one of its own (zenity's, Windows'): on an error with no
// list (SDL_GetError is that thread's), on a cancel with an empty one.
void on_pick(void* userdata, const char* const* list, int) {
    auto* s = static_cast<PickState*>(userdata);
    std::lock_guard<std::mutex> lk(s->mu);
    if (!list) {
        s->failed = true;
        const char* e = s->get_error ? s->get_error() : nullptr;
        s->error = e && *e ? e : "SDL gave no reason";
    } else if (list[0]) {
        s->chosen = list[0];
    }
    s->done = true;
}

}  // namespace

Pick Dialogs::pick(bool folder, const std::string& title, const std::string& filter_name,
                   const std::string& filter_pattern, std::string& chosen, std::string* why) {
    chosen.clear();
    if (!usable(why)) return Pick::Failed;
    PickState st;
    st.get_error = api_->GetError;
    // SDL reads the filter until it calls back (SDL_dialog.h), and this waits for that.
    Filter filter{filter_name.c_str(), filter_pattern.c_str()};
    const uint32_t props = api_->CreateProperties();
    if (!props) {
        if (why) *why = api_->error();
        return Pick::Failed;
    }
    api_->SetStringProperty(props, "SDL.filedialog.title", title.c_str());
    if (!folder && !filter_pattern.empty()) {
        api_->SetPointerProperty(props, "SDL.filedialog.filters", &filter);
        api_->SetNumberProperty(props, "SDL.filedialog.nfilters", 1);
    }
    api_->ShowFileDialogWithProperties(folder ? kOpenFolder : kOpenFile, on_pick, &st, props);
    api_->DestroyProperties(props);
    for (;;) {
        {
            std::lock_guard<std::mutex> lk(st.mu);
            if (st.done) break;
        }
        // The portal's reply arrives through SDL's event pump (D-Bus), on this thread.
        if (api_->inited) api_->PumpEvents();
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    std::lock_guard<std::mutex> lk(st.mu);
    if (st.failed) {
        if (why) *why = st.error;
        return Pick::Failed;
    }
    if (st.chosen.empty()) return Pick::Cancelled;
    chosen = st.chosen;
    return Pick::Chosen;
}

}  // namespace sa2d
