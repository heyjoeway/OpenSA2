/* Read-only SA2 PC Mod Loader probe for a private copy of an owned game.
 * Cross-compile as an x86 Windows DLL. No original code or assets are copied.
 * Addresses and offsets are from official SA2 Mod Loader headers and verified
 * x86 player code. This is a diagnostic, not part of the Unity player build.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <windows.h>

#define EXPORT __declspec(dllexport)

typedef struct {
    int32_t Version;
    void *Init, *Patches;
    int32_t PatchCount;
    void *Jumps;
    int32_t JumpCount;
    void *Calls;
    int32_t CallCount;
    void *Pointers;
    int32_t PointerCount;
} ModInfo;

EXPORT const ModInfo SA2ModInfo = {1, 0, 0, 0, 0, 0, 0, 0, 0, 0};

static FILE *trace_file;
static FILE *collision_file;
static FILE *candidate_file;
static FILE *precise_file;
static FILE *precise_branch_file;
static FILE *precise_face_file;
static FILE *precise_record_file;
static FILE *precise_selection_file;
static FILE *precise_matrix_file;
static unsigned long tick;
static unsigned long collision_sequence;
static unsigned patched_call_count;
static unsigned precise_call_index;
static unsigned patched_precise_call;
static unsigned patched_precise_branches;
static unsigned patched_precise_face;
static unsigned patched_precise_matrix;
static unsigned char probe_fx_state[512] __attribute__((aligned(16), used));

typedef struct {
    uint32_t sequence;
    uint32_t site;
    uint32_t eax;
    uint32_t esi;
    uint32_t esp18;
    uint32_t esp20;
    uint32_t esp34;
    uint32_t esp38;
    uint32_t esp3c;
    uint32_t esp64;
    uint32_t esp78;
    uint32_t esp80;
    uint32_t face_counter;
    uint32_t sample_counter;
    uint32_t accept_counter;
    uint32_t contact_count;
    uint32_t pni_cursor;
    uint32_t poly;
    uint32_t local_vertices[9];
    uint32_t local_origin[3];
    uint32_t local_sweep[3];
    uint32_t local_half_step[3];
    uint32_t padding[28];
} PreciseBranchEvent;

#define PRECISE_BRANCH_CAPACITY 4096
static volatile uint32_t precise_probe_active;
static volatile uint32_t precise_event_count;
static PreciseBranchEvent precise_events[PRECISE_BRANCH_CAPACITY]
    __attribute__((used));
_Static_assert(sizeof(PreciseBranchEvent) == 256,
               "PreciseBranchEvent assembly stride must stay 256 bytes");

typedef struct {
    uint32_t sequence;
    uint32_t pni_cursor;
    uint32_t poly;
    uint32_t remaining;
    uint32_t poly_words[16];
    uint32_t padding[12];
} PreciseFaceEvent;

#define PRECISE_FACE_CAPACITY 256
static volatile uint32_t precise_face_probe_active;
static volatile uint32_t precise_face_event_count;
static volatile uint32_t precise_face_overflow;
static PreciseFaceEvent precise_face_events[PRECISE_FACE_CAPACITY]
    __attribute__((used));
_Static_assert(sizeof(PreciseFaceEvent) == 128,
               "PreciseFaceEvent assembly stride must stay 128 bytes");

typedef struct {
    uint32_t sequence;
    uint32_t onframe_tick;
    uint32_t call_index;
    uint32_t record_index;
    uint32_t matrix_argument;
    uint32_t matrix_global_pre;
    uint32_t matrix_global_post;
    uint32_t candidate_cursor;
    uint32_t poly;
    uint32_t polatflg;
    uint32_t effective_radius;
    uint32_t local_origin[3];
    uint32_t local_sweep[3];
    uint32_t vertex0[3];
    uint32_t pre_matrix_words[12];
    uint32_t post_matrix_words[12];
    uint32_t padding[20];
} PreciseMatrixEvent;

#define PRECISE_MATRIX_CAPACITY 256
static volatile uint32_t precise_matrix_probe_active;
static volatile uint32_t precise_matrix_event_count;
static volatile uint32_t precise_matrix_overflow;
static PreciseMatrixEvent precise_matrix_events[PRECISE_MATRIX_CAPACITY]
    __attribute__((used));
_Static_assert(sizeof(PreciseMatrixEvent) == 256,
               "PreciseMatrixEvent assembly stride must stay 256 bytes");

typedef struct {
    uint32_t attribute;
    const void *object;
    const void *task;
} DynColInfo;

typedef struct {
    float radius;
    float pos[3];
    float spd[3];
    float tnorm[3];
    uint16_t find_count;
    uint16_t selected_nmb;
    float yt;
    float yb;
    int32_t angx;
    int32_t angz;
    float normal[3];
    float normal2[3];
    float onpoly[3];
    float pshbk[3];
    float anaspdh[3];
    float anaspdv[3];
} CollisionWork;

_Static_assert(sizeof(CollisionWork) == 132,
               "CollisionWork must remain the native 33-word csts layout");

typedef struct {
    unsigned long onframe_tick;
    unsigned long sequence;
    const unsigned char *entity;
    const float *travel;
    const unsigned char *player;
    uint8_t action;
    uint16_t status;
    float position[3];
    int32_t rotation[3];
    float travel_value[3];
    float local_speed[3];
    float wall_normal[3];
    float floor_normal[3];
    uint32_t current_flags;
    uint32_t previous_flags;
    int16_t global_candidate_count;
    CollisionWork work;
    int has_work;
} CollisionSnapshot;

static CollisionSnapshot collision_pre;

typedef struct {
    unsigned long sequence;
    unsigned long onframe_tick;
    unsigned call_index;
    const void *custom_argument;
    const CollisionWork *work;
    int mode;
    CollisionWork work_before;
    int has_work;
    uint32_t face_counter_before;
    uint32_t sample_counter_before;
    uint32_t accept_counter_before;
    const void *object_info;
    const void *object_pointer;
    uint32_t object_attribute;
    const void *pni_start;
    const void *pni_end;
    int32_t pni_count;
} PreciseCallSnapshot;

static PreciseCallSnapshot precise_pre;

static void copy3(float destination[3], const unsigned char *source, unsigned offset) {
    destination[0] = *(const float *)(source + offset);
    destination[1] = *(const float *)(source + offset + 4);
    destination[2] = *(const float *)(source + offset + 8);
}

static void ensure_collision_files(void) {
    if (!collision_file) {
        collision_file = fopen("mods/sa2-native-collision-step.csv", "w");
        if (collision_file) {
            fputs("sequence,onframe_tick,patched_calls,result,pre_action,post_action,pre_status,post_status,pre_px,pre_py,pre_pz,post_px,post_py,post_pz,pre_angx,pre_angy,pre_angz,post_angx,post_angy,post_angz,travel_x,travel_y,travel_z,pre_speed_x,pre_speed_y,pre_speed_z,post_speed_x,post_speed_y,post_speed_z,pre_current_flags,pre_previous_flags,post_current_flags,post_previous_flags,pre_floor_x,pre_floor_y,pre_floor_z,post_floor_x,post_floor_y,post_floor_z,pre_wall_x,pre_wall_y,pre_wall_z,post_wall_x,post_wall_y,post_wall_z,pre_candidate_count,post_candidate_count,pre_has_work,post_has_work,pre_radius,post_radius,pre_work_px,pre_work_py,pre_work_pz,post_work_px,post_work_py,post_work_pz,pre_work_spd_x,pre_work_spd_y,pre_work_spd_z,post_work_spd_x,post_work_spd_y,post_work_spd_z,pre_tnorm_x,pre_tnorm_y,pre_tnorm_z,post_tnorm_x,post_tnorm_y,post_tnorm_z,pre_find_count,post_find_count,pre_selected,post_selected,pre_yt,pre_yb,post_yt,post_yb,pre_work_angx,pre_work_angz,post_work_angx,post_work_angz,pre_normal_x,pre_normal_y,pre_normal_z,post_normal_x,post_normal_y,post_normal_z,pre_normal2_x,pre_normal2_y,pre_normal2_z,post_normal2_x,post_normal2_y,post_normal2_z,pre_onpoly_x,pre_onpoly_y,pre_onpoly_z,post_onpoly_x,post_onpoly_y,post_onpoly_z,pre_pshbk_x,pre_pshbk_y,pre_pshbk_z,post_pshbk_x,post_pshbk_y,post_pshbk_z,pre_anaspdh_x,pre_anaspdh_y,pre_anaspdh_z,post_anaspdh_x,post_anaspdh_y,post_anaspdh_z,pre_anaspdv_x,pre_anaspdv_y,pre_anaspdv_z,post_anaspdv_x,post_anaspdv_y,post_anaspdv_z\n", collision_file);
        }
    }
    if (!candidate_file) {
        candidate_file = fopen("mods/sa2-native-collision-candidates.csv", "w");
        if (candidate_file) {
            fputs("sequence,onframe_tick,index,attribute,object,task\n", candidate_file);
        }
    }
    if (!precise_file) {
        precise_file = fopen("mods/sa2-native-precise-contact.csv", "w");
        if (precise_file) {
            fputs("sequence,onframe_tick,call_index,patched_call,result,action,status,custom_argument,work,mode,has_work,radius,pre_pos_x,pre_pos_y,pre_pos_z,post_pos_x,post_pos_y,post_pos_z,pre_spd_x,pre_spd_y,pre_spd_z,post_spd_x,post_spd_y,post_spd_z,pre_tnorm_x,pre_tnorm_y,pre_tnorm_z,post_tnorm_x,post_tnorm_y,post_tnorm_z,pre_find_count,post_find_count,pre_selected,post_selected,pre_normal_x,pre_normal_y,pre_normal_z,post_normal_x,post_normal_y,post_normal_z,pre_onpoly_x,pre_onpoly_y,pre_onpoly_z,post_onpoly_x,post_onpoly_y,post_onpoly_z,pre_pshbk_x,pre_pshbk_y,pre_pshbk_z,post_pshbk_x,post_pshbk_y,post_pshbk_z\n", precise_file);
        }
    }
    if (!precise_branch_file) {
        precise_branch_file = fopen("mods/sa2-native-precise-branches.csv", "w");
        if (precise_branch_file) {
            fputs("sequence,onframe_tick,event_index,site,eax,ax,esi,esp18,esp20,esp34,esp38,esp3c,esp64,esp78,esp80,face_delta,sample_delta,accept_delta,contact_count,live_tolerance,patched_branches,pni_cursor,poly,lv0x,lv0y,lv0z,lv1x,lv1y,lv1z,lv2x,lv2y,lv2z,local_origin_x,local_origin_y,local_origin_z,local_sweep_x,local_sweep_y,local_sweep_z,local_half_step_x,local_half_step_y,local_half_step_z\n", precise_branch_file);
        }
    }
    if (!precise_face_file) {
        precise_face_file = fopen("mods/sa2-native-precise-faces.csv", "w");
        if (precise_face_file) {
            fputs("sequence,onframe_tick,call_index,visit_index,remaining,custom_argument,work,mode,objinfo,objptr,objatt,pni_start,pni_end,pni_nmb,pni_cursor,poly,next,before,cx_bits,cy_bits,cz_bits,ndlenmax2_bits,v0x_bits,v0y_bits,v0z_bits,v1x_bits,v1y_bits,v1z_bits,v2x_bits,v2y_bits,v2z_bits,polatflg,radius_bits,pos_x_bits,pos_y_bits,pos_z_bits,spd_x_bits,spd_y_bits,spd_z_bits,overflow,patched_face_hook\n", precise_face_file);
        }
    }
    if (!precise_record_file) {
        precise_record_file = fopen("mods/sa2-native-precise-records.csv", "w");
        if (precise_record_file) {
            fputs("sequence,onframe_tick,call_index,result,record_count,selected_nmb,record_index", precise_record_file);
            for (unsigned word = 0; word < 39; ++word)
                fprintf(precise_record_file, ",slot_w%u_bits", word);
            for (unsigned word = 0; word < 22; ++word)
                fprintf(precise_record_file, ",public_w%u_bits", word);
            fputc('\n', precise_record_file);
        }
    }
    if (!precise_selection_file) {
        precise_selection_file = fopen("mods/sa2-native-precise-selection.csv", "w");
        if (precise_selection_file) {
            fputs("sequence,onframe_tick,call_index,result,record_count,selected_nmb", precise_selection_file);
            for (unsigned word = 0; word < 33; ++word)
                fprintf(precise_selection_file, ",csts_w%u_bits", word);
            fputc('\n', precise_selection_file);
        }
    }
    if (!precise_matrix_file) {
        precise_matrix_file = fopen("mods/sa2-native-precise-matrices.csv", "w");
        if (precise_matrix_file) {
            fputs("sequence,onframe_tick,call_index,event_index,record_index,"
                  "matrix_argument,matrix_global_pre,matrix_global_post,"
                  "candidate_cursor,poly,polatflg,effective_radius_bits,"
                  "origin_x_bits,origin_y_bits,origin_z_bits,"
                  "sweep_x_bits,sweep_y_bits,sweep_z_bits,"
                  "v0_x_bits,v0_y_bits,v0_z_bits", precise_matrix_file);
            for (unsigned word = 0; word < 12; ++word)
                fprintf(precise_matrix_file, ",pre_w%u_bits", word);
            for (unsigned word = 0; word < 12; ++word)
                fprintf(precise_matrix_file, ",post_w%u_bits", word);
            fputs(",overflow,patched_matrix_hook\n", precise_matrix_file);
        }
    }
}

static int is_precise_record_sequence(unsigned long sequence) {
    return (sequence >= 172 && sequence <= 290) ||
           (sequence >= 397 && sequence <= 411);
}

static void snapshot_collision(CollisionSnapshot *snapshot,
                               const unsigned char *entity,
                               const float *travel,
                               const unsigned char *player) {
    const CollisionWork *work = 0;
    *snapshot = (CollisionSnapshot){0};
    snapshot->onframe_tick = tick;
    snapshot->sequence = collision_sequence;
    snapshot->entity = entity;
    snapshot->travel = travel;
    snapshot->player = player;
    snapshot->action = entity ? entity[0] : 0xff;
    snapshot->status = entity ? *(const uint16_t *)(entity + 4) : 0xffff;
    if (entity) {
        copy3(snapshot->position, entity, 0x14);
        snapshot->rotation[0] = *(const int32_t *)(entity + 8);
        snapshot->rotation[1] = *(const int32_t *)(entity + 0xc);
        snapshot->rotation[2] = *(const int32_t *)(entity + 0x10);
    }
    if (travel) {
        snapshot->travel_value[0] = travel[0];
        snapshot->travel_value[1] = travel[1];
        snapshot->travel_value[2] = travel[2];
    }
    if (player) {
        copy3(snapshot->local_speed, player, 0x64);
        copy3(snapshot->wall_normal, player, 0x70);
        copy3(snapshot->floor_normal, player, 0x7c);
        snapshot->current_flags = *(const uint32_t *)(player + 0x88);
        snapshot->previous_flags = *(const uint32_t *)(player + 0x8c);
        work = *(const CollisionWork *const *)(player + 0x90);
    }
    snapshot->global_candidate_count = *(const int16_t *)0x01DE9484;
    snapshot->has_work = work != 0;
    if (work) snapshot->work = *work;
}

static void write_vec3(FILE *file, const float value[3]) {
    fprintf(file, "%.9g,%.9g,%.9g", value[0], value[1], value[2]);
}

__attribute__((used, noinline))
static void capture_collision_pre(const unsigned char *entity,
                                  const float *travel,
                                  const unsigned char *player) {
    ++collision_sequence;
    precise_call_index = 0;
    ensure_collision_files();
    snapshot_collision(&collision_pre, entity, travel, player);
}

__attribute__((used, noinline))
static void capture_precise_pre(const void *custom_argument,
                                const CollisionWork *work,
                                int mode) {
    const unsigned char *object_info =
        *(const unsigned char *const *)0x01A5A400;
    precise_pre = (PreciseCallSnapshot){0};
    precise_pre.sequence = collision_sequence;
    precise_pre.onframe_tick = tick;
    precise_pre.call_index = precise_call_index++;
    precise_pre.custom_argument = custom_argument;
    precise_pre.work = work;
    precise_pre.mode = mode;
    precise_pre.has_work = work != 0;
    precise_pre.face_counter_before = *(const uint32_t *)0x01DD9490;
    precise_pre.sample_counter_before = *(const uint32_t *)0x01DD948C;
    precise_pre.accept_counter_before = *(const uint32_t *)0x01DD9470;
    if (work) precise_pre.work_before = *work;
    while (object_info &&
           *(const void *const *)(object_info + 0x08) != custom_argument) {
        object_info = *(const unsigned char *const *)object_info;
    }
    precise_pre.object_info = object_info;
    if (object_info) {
        precise_pre.object_pointer = *(const void *const *)(object_info + 0x08);
        precise_pre.object_attribute = *(const uint32_t *)(object_info + 0x0c);
        precise_pre.pni_start = *(const void *const *)(object_info + 0x1c);
        precise_pre.pni_end = *(const void *const *)(object_info + 0x20);
        precise_pre.pni_count = *(const int32_t *)(object_info + 0x24);
    }
    if (precise_pre.call_index == 0) {
        precise_event_count = 0;
        precise_face_event_count = 0;
        precise_face_overflow = 0;
        precise_probe_active =
            collision_sequence >= 396 && collision_sequence <= 400;
        precise_face_probe_active = precise_probe_active;
    }
    precise_matrix_event_count = 0;
    precise_matrix_overflow = 0;
    precise_matrix_probe_active =
        is_precise_record_sequence(collision_sequence);
}

static uint32_t float_bits(float value) {
    union {
        float as_float;
        uint32_t as_uint;
    } bits;
    bits.as_float = value;
    return bits.as_uint;
}

__attribute__((used, noinline))
static void capture_precise_post(int result) {
    CollisionWork after = {0};
    uint32_t event_count;
    uint32_t face_event_count;
    uint32_t record_count = *(const uint32_t *)0x019471B0;
    precise_probe_active = 0;
    precise_face_probe_active = 0;
    precise_matrix_probe_active = 0;
    if (precise_pre.work) after = *precise_pre.work;
    event_count = precise_event_count;
    if (event_count > PRECISE_BRANCH_CAPACITY) event_count = PRECISE_BRANCH_CAPACITY;
    if (precise_branch_file && precise_pre.call_index == 0) {
        for (uint32_t i = 0; i < event_count; ++i) {
            const PreciseBranchEvent *event = &precise_events[i];
            fprintf(precise_branch_file,
                    "%lu,%lu,%lu,0x%08lx,0x%08lx,0x%04lx,0x%08lx,"
                    "0x%08lx,0x%08lx,0x%08lx,0x%08lx,0x%08lx,0x%08lx,"
                    "0x%08lx,0x%08lx,%lu,%lu,%lu,%lu,%.9g,%u,"
                    "0x%08lx,0x%08lx,"
                    "0x%08lx,0x%08lx,0x%08lx,"
                    "0x%08lx,0x%08lx,0x%08lx,"
                    "0x%08lx,0x%08lx,0x%08lx,"
                    "0x%08lx,0x%08lx,0x%08lx,"
                    "0x%08lx,0x%08lx,0x%08lx,"
                    "0x%08lx,0x%08lx,0x%08lx\n",
                    precise_pre.sequence, precise_pre.onframe_tick,
                    (unsigned long)i, (unsigned long)event->site,
                    (unsigned long)event->eax,
                    (unsigned long)(event->eax & 0xffff),
                    (unsigned long)event->esi,
                    (unsigned long)event->esp18,
                    (unsigned long)event->esp20,
                    (unsigned long)event->esp34,
                    (unsigned long)event->esp38,
                    (unsigned long)event->esp3c,
                    (unsigned long)event->esp64,
                    (unsigned long)event->esp78,
                    (unsigned long)event->esp80,
                    (unsigned long)(event->face_counter -
                                    precise_pre.face_counter_before),
                    (unsigned long)(event->sample_counter -
                                    precise_pre.sample_counter_before),
                    (unsigned long)(event->accept_counter -
                                    precise_pre.accept_counter_before),
                    (unsigned long)event->contact_count,
                    *(const float *)0x015F706C,
                    patched_precise_branches,
                    (unsigned long)event->pni_cursor,
                    (unsigned long)event->poly,
                    (unsigned long)event->local_vertices[0],
                    (unsigned long)event->local_vertices[1],
                    (unsigned long)event->local_vertices[2],
                    (unsigned long)event->local_vertices[3],
                    (unsigned long)event->local_vertices[4],
                    (unsigned long)event->local_vertices[5],
                    (unsigned long)event->local_vertices[6],
                    (unsigned long)event->local_vertices[7],
                    (unsigned long)event->local_vertices[8],
                    (unsigned long)event->local_origin[0],
                    (unsigned long)event->local_origin[1],
                    (unsigned long)event->local_origin[2],
                    (unsigned long)event->local_sweep[0],
                    (unsigned long)event->local_sweep[1],
                    (unsigned long)event->local_sweep[2],
                    (unsigned long)event->local_half_step[0],
                    (unsigned long)event->local_half_step[1],
                    (unsigned long)event->local_half_step[2]);
        }
        fflush(precise_branch_file);
    }
    face_event_count = precise_face_event_count;
    if (face_event_count > PRECISE_FACE_CAPACITY)
        face_event_count = PRECISE_FACE_CAPACITY;
    if (precise_face_file && precise_pre.call_index == 0) {
        for (uint32_t i = 0; i < face_event_count; ++i) {
            const PreciseFaceEvent *event = &precise_face_events[i];
            fprintf(precise_face_file,
                    "%lu,%lu,%u,%lu,%lu,0x%08lx,0x%08lx,%d,"
                    "0x%08lx,0x%08lx,0x%08lx,0x%08lx,0x%08lx,%ld,"
                    "0x%08lx,0x%08lx,0x%08lx,0x%08lx,"
                    "0x%08lx,0x%08lx,0x%08lx,0x%08lx,"
                    "0x%08lx,0x%08lx,0x%08lx,0x%08lx,"
                    "0x%08lx,0x%08lx,0x%08lx,0x%08lx,0x%08lx,0x%08lx,"
                    "0x%08lx,0x%08lx,0x%08lx,0x%08lx,"
                    "0x%08lx,0x%08lx,0x%08lx,%lu,%u\n",
                    precise_pre.sequence, precise_pre.onframe_tick,
                    precise_pre.call_index, (unsigned long)(i + 1),
                    (unsigned long)event->remaining,
                    (unsigned long)(uintptr_t)precise_pre.custom_argument,
                    (unsigned long)(uintptr_t)precise_pre.work,
                    precise_pre.mode,
                    (unsigned long)(uintptr_t)precise_pre.object_info,
                    (unsigned long)(uintptr_t)precise_pre.object_pointer,
                    (unsigned long)precise_pre.object_attribute,
                    (unsigned long)(uintptr_t)precise_pre.pni_start,
                    (unsigned long)(uintptr_t)precise_pre.pni_end,
                    (long)precise_pre.pni_count,
                    (unsigned long)event->pni_cursor,
                    (unsigned long)event->poly,
                    (unsigned long)event->poly_words[0],
                    (unsigned long)event->poly_words[1],
                    (unsigned long)event->poly_words[2],
                    (unsigned long)event->poly_words[3],
                    (unsigned long)event->poly_words[4],
                    (unsigned long)event->poly_words[5],
                    (unsigned long)event->poly_words[6],
                    (unsigned long)event->poly_words[7],
                    (unsigned long)event->poly_words[8],
                    (unsigned long)event->poly_words[9],
                    (unsigned long)event->poly_words[10],
                    (unsigned long)event->poly_words[11],
                    (unsigned long)event->poly_words[12],
                    (unsigned long)event->poly_words[13],
                    (unsigned long)event->poly_words[14],
                    (unsigned long)event->poly_words[15],
                    (unsigned long)float_bits(precise_pre.work_before.radius),
                    (unsigned long)float_bits(precise_pre.work_before.pos[0]),
                    (unsigned long)float_bits(precise_pre.work_before.pos[1]),
                    (unsigned long)float_bits(precise_pre.work_before.pos[2]),
                    (unsigned long)float_bits(precise_pre.work_before.spd[0]),
                    (unsigned long)float_bits(precise_pre.work_before.spd[1]),
                    (unsigned long)float_bits(precise_pre.work_before.spd[2]),
                    (unsigned long)precise_face_overflow,
                    patched_precise_face);
        }
        fflush(precise_face_file);
    }
    if (is_precise_record_sequence(precise_pre.sequence)) {
        uint32_t bounded_count = record_count <= 128 ? record_count : 0;
        uint32_t matrix_count = precise_matrix_event_count;
        if (matrix_count > PRECISE_MATRIX_CAPACITY)
            matrix_count = PRECISE_MATRIX_CAPACITY;
        if (precise_matrix_file) {
            for (uint32_t i = 0; i < matrix_count; ++i) {
                const PreciseMatrixEvent *event = &precise_matrix_events[i];
                fprintf(precise_matrix_file,
                        "%lu,%lu,%lu,%lu,%lu,"
                        "0x%08lx,0x%08lx,0x%08lx,0x%08lx,0x%08lx,"
                        "0x%08lx,0x%08lx,"
                        "0x%08lx,0x%08lx,0x%08lx,"
                        "0x%08lx,0x%08lx,0x%08lx,"
                        "0x%08lx,0x%08lx,0x%08lx",
                        (unsigned long)event->sequence,
                        (unsigned long)event->onframe_tick,
                        (unsigned long)event->call_index,
                        (unsigned long)i,
                        (unsigned long)event->record_index,
                        (unsigned long)event->matrix_argument,
                        (unsigned long)event->matrix_global_pre,
                        (unsigned long)event->matrix_global_post,
                        (unsigned long)event->candidate_cursor,
                        (unsigned long)event->poly,
                        (unsigned long)event->polatflg,
                        (unsigned long)event->effective_radius,
                        (unsigned long)event->local_origin[0],
                        (unsigned long)event->local_origin[1],
                        (unsigned long)event->local_origin[2],
                        (unsigned long)event->local_sweep[0],
                        (unsigned long)event->local_sweep[1],
                        (unsigned long)event->local_sweep[2],
                        (unsigned long)event->vertex0[0],
                        (unsigned long)event->vertex0[1],
                        (unsigned long)event->vertex0[2]);
                for (unsigned word = 0; word < 12; ++word)
                    fprintf(precise_matrix_file, ",0x%08lx",
                            (unsigned long)event->pre_matrix_words[word]);
                for (unsigned word = 0; word < 12; ++word)
                    fprintf(precise_matrix_file, ",0x%08lx",
                            (unsigned long)event->post_matrix_words[word]);
                fprintf(precise_matrix_file, ",%lu,%u\n",
                        (unsigned long)precise_matrix_overflow,
                        patched_precise_matrix);
            }
            fflush(precise_matrix_file);
        }
        if (precise_record_file) {
            const unsigned char *scratch =
                (const unsigned char *)0x0194A234;
            const unsigned char *public_records =
                (const unsigned char *)0x01DDB660;
            for (uint32_t record = 0; record < bounded_count; ++record) {
                fprintf(precise_record_file,
                        "%lu,%lu,%u,%d,%lu,%u,%lu",
                        precise_pre.sequence, precise_pre.onframe_tick,
                        precise_pre.call_index, result,
                        (unsigned long)record_count,
                        (unsigned)after.selected_nmb,
                        (unsigned long)record);
                for (unsigned word = 0; word < 39; ++word) {
                    uint32_t bits;
                    memcpy(&bits, scratch + record * 0x9c + word * 4,
                           sizeof(bits));
                    fprintf(precise_record_file, ",0x%08lx",
                            (unsigned long)bits);
                }
                for (unsigned word = 0; word < 22; ++word) {
                    uint32_t bits;
                    memcpy(&bits, public_records + record * 0x58 + word * 4,
                           sizeof(bits));
                    fprintf(precise_record_file, ",0x%08lx",
                            (unsigned long)bits);
                }
                fputc('\n', precise_record_file);
            }
            fflush(precise_record_file);
        }
        if (precise_selection_file) {
            fprintf(precise_selection_file, "%lu,%lu,%u,%d,%lu,%u",
                    precise_pre.sequence, precise_pre.onframe_tick,
                    precise_pre.call_index, result,
                    (unsigned long)record_count,
                    (unsigned)after.selected_nmb);
            for (unsigned word = 0; word < 33; ++word) {
                uint32_t bits;
                memcpy(&bits, (const unsigned char *)&after + word * 4,
                       sizeof(bits));
                fprintf(precise_selection_file, ",0x%08lx",
                        (unsigned long)bits);
            }
            fputc('\n', precise_selection_file);
            fflush(precise_selection_file);
        }
    }
    if (!precise_file) return;
    fprintf(precise_file, "%lu,%lu,%u,%u,%d,%u,%u,0x%08lx,0x%08lx,%d,%d,%.9g,",
            precise_pre.sequence, precise_pre.onframe_tick, precise_pre.call_index,
            patched_precise_call, result,
            (unsigned)collision_pre.action, (unsigned)collision_pre.status,
            (unsigned long)(uintptr_t)precise_pre.custom_argument,
            (unsigned long)(uintptr_t)precise_pre.work,
            precise_pre.mode, precise_pre.has_work,
            precise_pre.work_before.radius);
    write_vec3(precise_file, precise_pre.work_before.pos); fputc(',', precise_file);
    write_vec3(precise_file, after.pos); fputc(',', precise_file);
    write_vec3(precise_file, precise_pre.work_before.spd); fputc(',', precise_file);
    write_vec3(precise_file, after.spd); fputc(',', precise_file);
    write_vec3(precise_file, precise_pre.work_before.tnorm); fputc(',', precise_file);
    write_vec3(precise_file, after.tnorm); fputc(',', precise_file);
    fprintf(precise_file, "%u,%u,%u,%u,",
            (unsigned)precise_pre.work_before.find_count, (unsigned)after.find_count,
            (unsigned)precise_pre.work_before.selected_nmb, (unsigned)after.selected_nmb);
    write_vec3(precise_file, precise_pre.work_before.normal); fputc(',', precise_file);
    write_vec3(precise_file, after.normal); fputc(',', precise_file);
    write_vec3(precise_file, precise_pre.work_before.onpoly); fputc(',', precise_file);
    write_vec3(precise_file, after.onpoly); fputc(',', precise_file);
    write_vec3(precise_file, precise_pre.work_before.pshbk); fputc(',', precise_file);
    write_vec3(precise_file, after.pshbk); fputc('\n', precise_file);
    fflush(precise_file);
}

__attribute__((used, noinline))
static void capture_collision_post(int result) {
    CollisionSnapshot post = {0};
    const DynColInfo *candidates;
    int count;
    int i;
    snapshot_collision(&post, collision_pre.entity, collision_pre.travel, collision_pre.player);
    if (collision_file) {
        fprintf(collision_file, "%lu,%lu,%u,%d,%u,%u,%u,%u,",
                collision_pre.sequence, collision_pre.onframe_tick, patched_call_count, result,
                (unsigned)collision_pre.action, (unsigned)post.action,
                (unsigned)collision_pre.status, (unsigned)post.status);
        write_vec3(collision_file, collision_pre.position); fputc(',', collision_file);
        write_vec3(collision_file, post.position); fputc(',', collision_file);
        fprintf(collision_file, "%d,%d,%d,%d,%d,%d,",
                collision_pre.rotation[0], collision_pre.rotation[1], collision_pre.rotation[2],
                post.rotation[0], post.rotation[1], post.rotation[2]);
        write_vec3(collision_file, collision_pre.travel_value); fputc(',', collision_file);
        write_vec3(collision_file, collision_pre.local_speed); fputc(',', collision_file);
        write_vec3(collision_file, post.local_speed); fputc(',', collision_file);
        fprintf(collision_file, "%u,%u,%u,%u,",
                collision_pre.current_flags, collision_pre.previous_flags,
                post.current_flags, post.previous_flags);
        write_vec3(collision_file, collision_pre.floor_normal); fputc(',', collision_file);
        write_vec3(collision_file, post.floor_normal); fputc(',', collision_file);
        write_vec3(collision_file, collision_pre.wall_normal); fputc(',', collision_file);
        write_vec3(collision_file, post.wall_normal); fputc(',', collision_file);
        fprintf(collision_file, "%d,%d,%d,%d,%.9g,%.9g,",
                (int)collision_pre.global_candidate_count, (int)post.global_candidate_count,
                collision_pre.has_work, post.has_work,
                collision_pre.work.radius, post.work.radius);
        write_vec3(collision_file, collision_pre.work.pos); fputc(',', collision_file);
        write_vec3(collision_file, post.work.pos); fputc(',', collision_file);
        write_vec3(collision_file, collision_pre.work.spd); fputc(',', collision_file);
        write_vec3(collision_file, post.work.spd); fputc(',', collision_file);
        write_vec3(collision_file, collision_pre.work.tnorm); fputc(',', collision_file);
        write_vec3(collision_file, post.work.tnorm); fputc(',', collision_file);
        fprintf(collision_file, "%u,%u,%u,%u,%.9g,%.9g,%.9g,%.9g,%d,%d,%d,%d,",
                (unsigned)collision_pre.work.find_count, (unsigned)post.work.find_count,
                (unsigned)collision_pre.work.selected_nmb, (unsigned)post.work.selected_nmb,
                collision_pre.work.yt, collision_pre.work.yb, post.work.yt, post.work.yb,
                collision_pre.work.angx, collision_pre.work.angz,
                post.work.angx, post.work.angz);
        write_vec3(collision_file, collision_pre.work.normal); fputc(',', collision_file);
        write_vec3(collision_file, post.work.normal); fputc(',', collision_file);
        write_vec3(collision_file, collision_pre.work.normal2); fputc(',', collision_file);
        write_vec3(collision_file, post.work.normal2); fputc(',', collision_file);
        write_vec3(collision_file, collision_pre.work.onpoly); fputc(',', collision_file);
        write_vec3(collision_file, post.work.onpoly); fputc(',', collision_file);
        write_vec3(collision_file, collision_pre.work.pshbk); fputc(',', collision_file);
        write_vec3(collision_file, post.work.pshbk); fputc(',', collision_file);
        write_vec3(collision_file, collision_pre.work.anaspdh); fputc(',', collision_file);
        write_vec3(collision_file, post.work.anaspdh); fputc(',', collision_file);
        write_vec3(collision_file, collision_pre.work.anaspdv); fputc(',', collision_file);
        write_vec3(collision_file, post.work.anaspdv); fputc('\n', collision_file);
        fflush(collision_file);
    }
    candidates = *(const DynColInfo *const *)0x01A5A2DC;
    count = post.global_candidate_count;
    if (count < 0) count = 0;
    if (count > 256) count = 256;
    if (candidate_file && candidates) {
        for (i = 0; i < count; ++i) {
            fprintf(candidate_file, "%lu,%lu,%d,%u,0x%08lx,0x%08lx\n",
                    collision_pre.sequence, collision_pre.onframe_tick, i,
                    (unsigned)candidates[i].attribute,
                    (unsigned long)(uintptr_t)candidates[i].object,
                    (unsigned long)(uintptr_t)candidates[i].task);
        }
        fflush(candidate_file);
    }
}

/* Every SonicMain call site supplies entity in EAX, then travel and player as
 * stack arguments. Preserve that usercall convention around the two C probes. */
__attribute__((naked, used))
static int __cdecl PSetPositionTraceHook(void) {
    __asm__ __volatile__(
        "pushfl\n\t"
        "pushal\n\t"
        "fxsave _probe_fx_state\n\t"
        "pushl 44(%esp)\n\t"
        "pushl 44(%esp)\n\t"
        "pushl 36(%esp)\n\t"
        "call _capture_collision_pre\n\t"
        "addl $12, %esp\n\t"
        "fxrstor _probe_fx_state\n\t"
        "popal\n\t"
        "popfl\n\t"
        "pushl %ecx\n\t"
        "pushl 12(%esp)\n\t"
        "pushl 12(%esp)\n\t"
        "movl $0x004616e0, %ecx\n\t"
        "call *%ecx\n\t"
        "addl $8, %esp\n\t"
        "popl %ecx\n\t"
        "pushfl\n\t"
        "pushal\n\t"
        "fxsave _probe_fx_state\n\t"
        "pushl 28(%esp)\n\t"
        "call _capture_collision_post\n\t"
        "addl $4, %esp\n\t"
        "fxrstor _probe_fx_state\n\t"
        "popal\n\t"
        "popfl\n\t"
        "ret\n\t");
}

/* GroundCollisionStep callsite 0x46250A supplies a custom argument in EAX,
 * collision work and mode on the stack. Preserve all inputs and the result. */
__attribute__((naked, used))
static int __cdecl PreciseContactTraceHook(void) {
    __asm__ __volatile__(
        "pushfl\n\t"
        "pushal\n\t"
        "fxsave _probe_fx_state\n\t"
        "pushl 44(%esp)\n\t"
        "pushl 44(%esp)\n\t"
        "pushl 36(%esp)\n\t"
        "call _capture_precise_pre\n\t"
        "addl $12, %esp\n\t"
        "fxrstor _probe_fx_state\n\t"
        "popal\n\t"
        "popfl\n\t"
        "pushl %ecx\n\t"
        "pushl 12(%esp)\n\t"
        "pushl 12(%esp)\n\t"
        "movl $0x0048ce40, %ecx\n\t"
        "call *%ecx\n\t"
        "addl $8, %esp\n\t"
        "popl %ecx\n\t"
        "pushfl\n\t"
        "pushal\n\t"
        "fxsave _probe_fx_state\n\t"
        "pushl 28(%esp)\n\t"
        "call _capture_precise_post\n\t"
        "addl $4, %esp\n\t"
        "fxrstor _probe_fx_state\n\t"
        "popal\n\t"
        "popfl\n\t"
        "ret\n\t");
}

/* Internal branch hooks jump here with the original stack untouched. The site
 * address is the sole stack argument. This logger uses integer instructions
 * only, preserves flags/registers, and never disturbs the live x87 stack. */
__attribute__((naked, used))
static void __cdecl PreciseBranchLog(void) {
    __asm__ __volatile__(
        "pushfl\n\t"
        "pushal\n\t"
        "cmpl $0, _precise_probe_active\n\t"
        "je 1f\n\t"
        "movl _precise_event_count, %eax\n\t"
        "cmpl $4096, %eax\n\t"
        "jae 1f\n\t"
        "shll $8, %eax\n\t"
        "leal _precise_events(%eax), %edx\n\t"
        "movl _precise_pre, %ecx\n\t"
        "movl %ecx, 0(%edx)\n\t"
        "movl 40(%esp), %ecx\n\t"
        "movl %ecx, 4(%edx)\n\t"
        "movl 28(%esp), %ecx\n\t"
        "movl %ecx, 8(%edx)\n\t"
        "movl 4(%esp), %ecx\n\t"
        "movl %ecx, 12(%edx)\n\t"
        "leal 44(%esp), %ecx\n\t"
        "movl 0x18(%ecx), %eax\n\t"
        "movl %eax, 16(%edx)\n\t"
        "movl 0x20(%ecx), %eax\n\t"
        "movl %eax, 20(%edx)\n\t"
        "movl 0x34(%ecx), %eax\n\t"
        "movl %eax, 24(%edx)\n\t"
        "movl 0x38(%ecx), %eax\n\t"
        "movl %eax, 28(%edx)\n\t"
        "movl 0x3c(%ecx), %eax\n\t"
        "movl %eax, 32(%edx)\n\t"
        "movl 0x64(%ecx), %eax\n\t"
        "movl %eax, 36(%edx)\n\t"
        "movl 0x78(%ecx), %eax\n\t"
        "movl %eax, 40(%edx)\n\t"
        "movl 0x80(%ecx), %eax\n\t"
        "movl %eax, 44(%edx)\n\t"
        "movl 0x01dd9490, %eax\n\t"
        "movl %eax, 48(%edx)\n\t"
        "movl 0x01dd948c, %eax\n\t"
        "movl %eax, 52(%edx)\n\t"
        "movl 0x01dd9470, %eax\n\t"
        "movl %eax, 56(%edx)\n\t"
        "movl 0x019471b0, %eax\n\t"
        "movl %eax, 60(%edx)\n\t"
        "movl 16(%esp), %eax\n\t"
        "movl %eax, 64(%edx)\n\t"
        "movl (%eax), %eax\n\t"
        "movl %eax, 68(%edx)\n\t"
        "movl 0x0194a20c, %eax\n\t"
        "movl %eax, 72(%edx)\n\t"
        "movl 0x0194a210, %eax\n\t"
        "movl %eax, 76(%edx)\n\t"
        "movl 0x0194a214, %eax\n\t"
        "movl %eax, 80(%edx)\n\t"
        "movl 0x0194a218, %eax\n\t"
        "movl %eax, 84(%edx)\n\t"
        "movl 0x0194a21c, %eax\n\t"
        "movl %eax, 88(%edx)\n\t"
        "movl 0x0194a220, %eax\n\t"
        "movl %eax, 92(%edx)\n\t"
        "movl 0x0194a224, %eax\n\t"
        "movl %eax, 96(%edx)\n\t"
        "movl 0x0194a228, %eax\n\t"
        "movl %eax, 100(%edx)\n\t"
        "movl 0x0194a22c, %eax\n\t"
        "movl %eax, 104(%edx)\n\t"
        "movl 0x60(%ecx), %eax\n\t"
        "movl %eax, 108(%edx)\n\t"
        "movl 0x64(%ecx), %eax\n\t"
        "movl %eax, 112(%edx)\n\t"
        "movl 0x68(%ecx), %eax\n\t"
        "movl %eax, 116(%edx)\n\t"
        "movl 0x40(%ecx), %eax\n\t"
        "movl %eax, 120(%edx)\n\t"
        "movl 0x44(%ecx), %eax\n\t"
        "movl %eax, 124(%edx)\n\t"
        "movl 0x48(%ecx), %eax\n\t"
        "movl %eax, 128(%edx)\n\t"
        "movl 0x50(%ecx), %eax\n\t"
        "movl %eax, 132(%edx)\n\t"
        "movl 0x54(%ecx), %eax\n\t"
        "movl %eax, 136(%edx)\n\t"
        "movl 0x58(%ecx), %eax\n\t"
        "movl %eax, 140(%edx)\n\t"
        "incl _precise_event_count\n\t"
        "1:\n\t"
        "popal\n\t"
        "popfl\n\t"
        "ret\n\t");
}

/* Face capture runs before the first per-polygon FLD. The hook has already
 * replayed the original EDX/EAX loads, so this logger can copy the complete
 * CL_PolyInfo as raw dwords without touching the empty x87 stack or calling C. */
__attribute__((naked, used))
static void __cdecl PreciseFaceLog(void) {
    __asm__ __volatile__(
        "pushfl\n\t"
        "pushal\n\t"
        "cmpl $0, _precise_face_probe_active\n\t"
        "je 2f\n\t"
        "movl _precise_face_event_count, %eax\n\t"
        "cmpl $256, %eax\n\t"
        "jb 1f\n\t"
        "movl $1, _precise_face_overflow\n\t"
        "jmp 2f\n\t"
        "1:\n\t"
        "shll $7, %eax\n\t"
        "leal _precise_face_events(%eax), %edx\n\t"
        "movl _precise_pre, %ecx\n\t"
        "movl %ecx, 0(%edx)\n\t"
        "movl 20(%esp), %ecx\n\t"
        "movl %ecx, 4(%edx)\n\t"
        "movl 28(%esp), %ecx\n\t"
        "movl %ecx, 8(%edx)\n\t"
        "leal 40(%esp), %ecx\n\t"
        "movl 0x4c(%ecx), %eax\n\t"
        "movl %eax, 12(%edx)\n\t"
        "movl 28(%esp), %esi\n\t"
        "leal 16(%edx), %edi\n\t"
        "movl $16, %ecx\n\t"
        "cld\n\t"
        "rep movsl\n\t"
        "incl _precise_face_event_count\n\t"
        "2:\n\t"
        "popal\n\t"
        "popfl\n\t"
        "ret\n\t");
}

__attribute__((naked, used))
static void PreciseFaceHook(void) {
    __asm__ __volatile__(
        "movl 0x20(%esp), %edx\n\t"
        "movl (%edx), %eax\n\t"
        "call _PreciseFaceLog\n\t"
        "pushl $0x0048cef0\n\t"
        "ret\n\t");
}

/* Replace only the CALL displacement at 0x48DA4C. The wrapper snapshots the
 * active affine matrix before and after the installed in-place inverse using
 * integer instructions only. The original argument remains on the stack for
 * the caller's ADD ESP,4 at 0x48DA5C. */
__attribute__((naked, used))
static void PreciseMatrixHook(void) {
    __asm__ __volatile__(
        "pushfl\n\t"
        "pushal\n\t"
        "cmpl $0, _precise_matrix_probe_active\n\t"
        "je 3f\n\t"
        "movl _precise_matrix_event_count, %eax\n\t"
        "cmpl $256, %eax\n\t"
        "jb 1f\n\t"
        "movl $1, _precise_matrix_overflow\n\t"
        "jmp 3f\n\t"
        "1:\n\t"
        "shll $8, %eax\n\t"
        "leal _precise_matrix_events(%eax), %edx\n\t"
        "movl _precise_pre, %eax\n\t"
        "movl %eax, 0(%edx)\n\t"
        "movl _precise_pre+4, %eax\n\t"
        "movl %eax, 4(%edx)\n\t"
        "movl _precise_pre+8, %eax\n\t"
        "movl %eax, 8(%edx)\n\t"
        "movl 0x019471b0, %eax\n\t"
        "movl %eax, 12(%edx)\n\t"
        "movl 40(%esp), %esi\n\t"
        "movl %esi, 16(%edx)\n\t"
        "movl 0x01a557fc, %eax\n\t"
        "movl %eax, 20(%edx)\n\t"
        "movl 16(%esp), %eax\n\t"
        "movl %eax, 28(%edx)\n\t"
        "movl (%eax), %eax\n\t"
        "movl %eax, 32(%edx)\n\t"
        "movl 0x3c(%eax), %ecx\n\t"
        "movl %ecx, 36(%edx)\n\t"
        "movl 68(%esp), %ecx\n\t"
        "movl %ecx, 40(%edx)\n\t"
        "movl 140(%esp), %ecx\n\t"
        "movl %ecx, 44(%edx)\n\t"
        "movl 144(%esp), %ecx\n\t"
        "movl %ecx, 48(%edx)\n\t"
        "movl 148(%esp), %ecx\n\t"
        "movl %ecx, 52(%edx)\n\t"
        "movl 108(%esp), %ecx\n\t"
        "movl %ecx, 56(%edx)\n\t"
        "movl 112(%esp), %ecx\n\t"
        "movl %ecx, 60(%edx)\n\t"
        "movl 116(%esp), %ecx\n\t"
        "movl %ecx, 64(%edx)\n\t"
        "movl 0x18(%eax), %ecx\n\t"
        "movl %ecx, 68(%edx)\n\t"
        "movl 0x1c(%eax), %ecx\n\t"
        "movl %ecx, 72(%edx)\n\t"
        "movl 0x20(%eax), %ecx\n\t"
        "movl %ecx, 76(%edx)\n\t"
        "leal 80(%edx), %edi\n\t"
        "movl $12, %ecx\n\t"
        "cld\n\t"
        "rep movsl\n\t"
        "3:\n\t"
        "popal\n\t"
        "popfl\n\t"
        "pushl 4(%esp)\n\t"
        "movl 8(%esp), %ecx\n\t"
        "movl $0x00427040, %eax\n\t"
        "call *%eax\n\t"
        "addl $4, %esp\n\t"
        "pushfl\n\t"
        "pushal\n\t"
        "cmpl $0, _precise_matrix_probe_active\n\t"
        "je 5f\n\t"
        "movl _precise_matrix_event_count, %eax\n\t"
        "cmpl $256, %eax\n\t"
        "jae 5f\n\t"
        "shll $8, %eax\n\t"
        "leal _precise_matrix_events(%eax), %edx\n\t"
        "movl 0x01a557fc, %esi\n\t"
        "movl %esi, 24(%edx)\n\t"
        "leal 128(%edx), %edi\n\t"
        "movl $12, %ecx\n\t"
        "cld\n\t"
        "rep movsl\n\t"
        "incl _precise_matrix_event_count\n\t"
        "5:\n\t"
        "popal\n\t"
        "popfl\n\t"
        "ret\n\t");
}

#define BRANCH_HOOK(name, site, test, jump, fallthrough, target) \
    __attribute__((naked, used)) static void name(void) { \
        __asm__ __volatile__( \
            "pushl $" #site "\n\t" \
            "call _PreciseBranchLog\n\t" \
            "addl $4, %esp\n\t" \
            test "\n\t" \
            jump " 1f\n\t" \
            "pushl $" #fallthrough "\n\t" \
            "ret\n\t" \
            "1:\n\t" \
            "pushl $" #target "\n\t" \
            "ret\n\t"); \
    }

BRANCH_HOOK(PreciseBranchD1E9, 0x0048d1e9, "testb $0x41, %ah", "jnz", 0x0048d1ee, 0x0048d1fc)
BRANCH_HOOK(PreciseBranchD30C, 0x0048d30c, "testb $0x41, %ah", "jp",  0x0048d311, 0x0048d33d)
BRANCH_HOOK(PreciseBranchD358, 0x0048d358, "testb $0x01, %ah", "jnz", 0x0048d361, 0x0048d8be)
BRANCH_HOOK(PreciseBranchD613, 0x0048d613, "testb $0x41, %ah", "jp",  0x0048d61c, 0x0048d85f)
BRANCH_HOOK(PreciseBranchD628, 0x0048d628, "testb $0x01, %ah", "jnz", 0x0048d631, 0x0048d865)
BRANCH_HOOK(PreciseBranchD63B, 0x0048d63b, "testb $0x41, %ah", "jp",  0x0048d644, 0x0048d869)
BRANCH_HOOK(PreciseBranchD87B, 0x0048d87b, "testl %esi, %esi", "jz", 0x0048d883, 0x0048dbb0)

typedef struct {
    unsigned char *site;
    const unsigned char *expected;
    unsigned length;
    const void *hook;
} PreciseBranchPatch;

static int patch_precise_branches(void) {
    static const unsigned char face[] = {0x8b, 0x54, 0x24, 0x20, 0x8b, 0x02};
    static const unsigned char d1e9[] = {0xf6, 0xc4, 0x41, 0x75, 0x0e};
    static const unsigned char d30c[] = {0xf6, 0xc4, 0x41, 0x7a, 0x2c};
    static const unsigned char d358[] = {0xf6, 0xc4, 0x01, 0x0f, 0x85, 0x5d, 0x05, 0x00, 0x00};
    static const unsigned char d613[] = {0xf6, 0xc4, 0x41, 0x0f, 0x8a, 0x43, 0x02, 0x00, 0x00};
    static const unsigned char d628[] = {0xf6, 0xc4, 0x01, 0x0f, 0x85, 0x34, 0x02, 0x00, 0x00};
    static const unsigned char d63b[] = {0xf6, 0xc4, 0x41, 0x0f, 0x8a, 0x25, 0x02, 0x00, 0x00};
    static const unsigned char d87b[] = {0x85, 0xf6, 0x0f, 0x84, 0x2d, 0x03, 0x00, 0x00};
    const PreciseBranchPatch patches[] = {
        {(unsigned char *)0x0048CEEA, face, sizeof(face), &PreciseFaceHook},
        {(unsigned char *)0x0048D1E9, d1e9, sizeof(d1e9), &PreciseBranchD1E9},
        {(unsigned char *)0x0048D30C, d30c, sizeof(d30c), &PreciseBranchD30C},
        {(unsigned char *)0x0048D358, d358, sizeof(d358), &PreciseBranchD358},
        {(unsigned char *)0x0048D613, d613, sizeof(d613), &PreciseBranchD613},
        {(unsigned char *)0x0048D628, d628, sizeof(d628), &PreciseBranchD628},
        {(unsigned char *)0x0048D63B, d63b, sizeof(d63b), &PreciseBranchD63B},
        {(unsigned char *)0x0048D87B, d87b, sizeof(d87b), &PreciseBranchD87B},
    };
    DWORD old_protection;
    DWORD ignored;
    for (unsigned i = 0; i < sizeof(patches) / sizeof(patches[0]); ++i) {
        if (memcmp(patches[i].site, patches[i].expected, patches[i].length) != 0) return 0;
    }
    if (!VirtualProtect((void *)0x0048C000, 0x2000,
                        PAGE_EXECUTE_READWRITE, &old_protection)) return 0;
    for (unsigned i = 0; i < sizeof(patches) / sizeof(patches[0]); ++i) {
        int32_t displacement =
            (const unsigned char *)patches[i].hook - (patches[i].site + 5);
        patches[i].site[0] = 0xe9;
        *(int32_t *)(patches[i].site + 1) = displacement;
        for (unsigned j = 5; j < patches[i].length; ++j) patches[i].site[j] = 0x90;
    }
    VirtualProtect((void *)0x0048C000, 0x2000, old_protection, &ignored);
    FlushInstructionCache(GetCurrentProcess(), (const void *)0x0048C000, 0x2000);
    patched_precise_face = 1;
    patched_precise_branches = sizeof(patches) / sizeof(patches[0]) - 1;
    return 1;
}

EXPORT void __cdecl Init(const char *path, const void *helper_functions, unsigned int mod_index) {
    unsigned char *callsites[64];
    unsigned count = 0;
    unsigned char *site;
    unsigned char *precise_site = (unsigned char *)0x0046250A;
    unsigned char *inverse_site = (unsigned char *)0x0048DA4C;
    (void)path;
    (void)helper_functions;
    (void)mod_index;
    for (site = (unsigned char *)0x00717840; site + 5 <= (unsigned char *)0x00719459; ++site) {
        if (*site == 0xe8) {
            int32_t displacement = *(const int32_t *)(site + 1);
            const unsigned char *target = site + 5 + displacement;
            if (target == (const unsigned char *)0x004616E0 && count < 64) {
                callsites[count++] = site;
            }
        }
    }
    if (count != 22 || *precise_site != 0xe8 || *inverse_site != 0xe8 ||
        precise_site + 5 + *(const int32_t *)(precise_site + 1) !=
            (unsigned char *)0x0048CE40 ||
        inverse_site + 5 + *(const int32_t *)(inverse_site + 1) !=
            (unsigned char *)0x00427040) return;
    if (!patch_precise_branches()) return;
    for (unsigned i = 0; i < count; ++i) {
        DWORD old_protection;
        DWORD ignored;
        int32_t displacement = (unsigned char *)&PSetPositionTraceHook - (callsites[i] + 5);
        if (!VirtualProtect(callsites[i] + 1, 4, PAGE_EXECUTE_READWRITE, &old_protection)) return;
        *(int32_t *)(callsites[i] + 1) = displacement;
        VirtualProtect(callsites[i] + 1, 4, old_protection, &ignored);
    }
    {
        DWORD old_protection;
        DWORD ignored;
        int32_t displacement = (unsigned char *)&PreciseContactTraceHook - (precise_site + 5);
        if (!VirtualProtect(precise_site + 1, 4, PAGE_EXECUTE_READWRITE, &old_protection)) return;
        *(int32_t *)(precise_site + 1) = displacement;
        VirtualProtect(precise_site + 1, 4, old_protection, &ignored);
    }
    {
        DWORD old_protection;
        DWORD ignored;
        int32_t displacement =
            (unsigned char *)&PreciseMatrixHook - (inverse_site + 5);
        if (!VirtualProtect(inverse_site + 1, 4, PAGE_EXECUTE_READWRITE,
                            &old_protection)) return;
        *(int32_t *)(inverse_site + 1) = displacement;
        VirtualProtect(inverse_site + 1, 4, old_protection, &ignored);
    }
    FlushInstructionCache(GetCurrentProcess(), (const void *)0x00717840, 0x1c19);
    FlushInstructionCache(GetCurrentProcess(), precise_site, 5);
    FlushInstructionCache(GetCurrentProcess(), inverse_site, 5);
    patched_call_count = count;
    patched_precise_call = 1;
    patched_precise_matrix = 1;
}

static float f32(const unsigned char *base, unsigned offset) {
    return *(const float *)(base + offset);
}

static uint32_t u32(const unsigned char *base, unsigned offset) {
    return *(const uint32_t *)(base + offset);
}

EXPORT void __cdecl OnFrame(void) {
    const unsigned char *player = ((const unsigned char *const *)0x01DE9600)[0];
    const unsigned char *entity = ((const unsigned char *const *)0x01DEA6C0)[0];
    const unsigned char *entity2 = ((const unsigned char *const *)0x01DE95E0)[0];
    const unsigned char *controller = (const unsigned char *)0x01DEFC00;
    const unsigned char *raw = (const unsigned char *)0x01A52918;
    if (!trace_file) {
        trace_file = fopen("mods/sa2-native-trace.csv", "w");
        if (!trace_file) return;
        fputs("tick,player,entity,action,status,px,py,pz,yaw,stored_speed,local_x,local_y,local_z,acc_x,acc_y,acc_z,up,grip,nframe,mtnmode,anim_current,processed_x1,processed_y1,raw_x1,raw_y1,processed_x2,processed_y2,raw_x2,raw_y2,processed_on,raw_on,projected_yaw,projected_strength,field_38,run_accel,slow_down,run_brake,ground_frict,air_resist,max_x_spd,jog_speed,run_speed,jump_speed,jump_add,weight,floor_x,floor_y,floor_z,gravity_x,gravity_y,gravity_z,entity_acc_x,entity_acc_y,entity_acc_z,entity_vel_x,entity_vel_y,entity_vel_z,surface_flags,disable_control_timer,jump_held,jump_off,jump_released,jump_pressed,action_held,action_off,action_released,action_pressed,homing_phase,homing_target_state,homing_timer,camera_target_mode,camera_collision,camera_rotate,camera_pos_x,camera_pos_y,camera_pos_z,camera_ang_x,camera_ang_y,camera_ang_z,camera_dir_x,camera_dir_y,camera_dir_z,camera_tgt_x,camera_tgt_y,camera_tgt_z,camera_speed,camera_fov_current,camera_fov_target,camera_fov_speed,camera_analog_control,camera_player_stop,camera_view_x,camera_view_y,camera_view_z,camera_view_dir_x,camera_view_dir_y,camera_view_dir_z,camera_view_roll,camera_view_fov,camera_location_x,camera_location_y,camera_location_z,camera_location_ang_x,camera_location_ang_y,camera_location_ang_z,camera_location_dir_x,camera_location_dir_y,camera_location_dir_z,camera_location_tgt_x,camera_location_tgt_y,camera_location_tgt_z,camera_location_diff_x,camera_location_diff_y,camera_location_diff_z,camera_location_speed,camera_slot,camera_mode,camera_mode_timer,camera_adjust,camera_adjust_timer\n", trace_file);
    }
    ++tick;
    if (!player || !entity) {
        fprintf(trace_file, "%lu,%u,%u\n", tick, player != 0, entity != 0);
    } else {
        fprintf(trace_file,
            "%lu,1,1,%u,%u,%.9g,%.9g,%.9g,%u,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%u,%u,%d,%.9g,",
            tick, (unsigned)entity[0], (unsigned)*(const uint16_t *)(entity + 4),
            f32(entity, 0x14), f32(entity, 0x18), f32(entity, 0x1c), u32(entity, 0xc) & 0xffff,
            f32(player, 0x44), f32(player, 0x64), f32(player, 0x68), f32(player, 0x6c),
            f32(player, 0x58), f32(player, 0x5c), f32(player, 0x60),
            f32(player, 0x34), f32(player, 0x40), f32(player, 0x174 + 0x10),
            (int)*(const int16_t *)(player + 0x174),
            (int)*(const int16_t *)(player + 0x174 + 4),
            (int)*(const int16_t *)(controller + 0x1c),
            (int)*(const int16_t *)(controller + 0x1e),
            (int)*(const int16_t *)(raw + 0x1c), (int)*(const int16_t *)(raw + 0x1e),
            (int)*(const int16_t *)(controller + 0x20), (int)*(const int16_t *)(controller + 0x22),
            (int)*(const int16_t *)(raw + 0x20), (int)*(const int16_t *)(raw + 0x22),
            u32(controller, 8), u32(raw, 8),
            *(const int32_t *)0x01DEFBA0, *(const float *)0x01DEFBA4);
        fprintf(trace_file, "%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,",
            f32(player, 0x38), f32(player, 0xfc), f32(player, 0x104),
            f32(player, 0x108), f32(player, 0x120), f32(player, 0x114),
            f32(player, 0xd0), f32(player, 0xe4), f32(player, 0xe8),
            f32(player, 0xd8), f32(player, 0xf8), f32(player, 0x138));
        fprintf(trace_file, "%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%u,%d,%u,%u,%u,%u,%u,%u,%u,%u,%u,%d,%d,",
            f32(player, 0x7c), f32(player, 0x80), f32(player, 0x84),
            *(const float *)0x01DE94A0, *(const float *)0x01DE94A4,
            *(const float *)0x01DE94A8,
            entity2 ? f32(entity2, 0xc) : 0, entity2 ? f32(entity2, 0x10) : 0,
            entity2 ? f32(entity2, 0x14) : 0,
            entity2 ? f32(entity2, 0) : 0, entity2 ? f32(entity2, 4) : 0,
            entity2 ? f32(entity2, 8) : 0, u32(player, 0x88),
            (int)*(const int16_t *)(player + 0x14),
            (unsigned)*(const uint8_t *)0x01DEFAA4,
            (unsigned)*(const uint8_t *)0x01DEFAA6,
            (unsigned)*(const uint8_t *)0x01DEFB40,
            (unsigned)*(const uint8_t *)0x01DEFB80,
            (unsigned)*(const uint8_t *)0x01DEFBF0,
            (unsigned)*(const uint8_t *)0x01DEFB20,
            (unsigned)*(const uint8_t *)0x01DEFB42,
            (unsigned)*(const uint8_t *)0x01DEFB00,
            (unsigned)*(const uint8_t *)(player + 0x35e),
            (int)*(const int16_t *)(player + 0x364),
            (int)*(const int16_t *)(player + 0x366));
        {
            const unsigned char *camera = (const unsigned char *)0x01DCFF40;
            int slot = *(const int32_t *)(camera + 0x254);
            const unsigned char *mode = camera + 0x258 + (slot >= 0 && slot < 16 ? slot : 0) * 0x114;
            const unsigned char *adjust = camera + 0x1398 + (slot >= 0 && slot < 16 ? slot : 0) * 0x114;
            fprintf(trace_file,
                "%d,%d,%d,%.9g,%.9g,%.9g,%d,%d,%d,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,",
                *(const int32_t *)0x01DCFF00, *(const int32_t *)0x01DCFF04,
                *(const int32_t *)0x01DCFF08, *(const float *)0x01DCFF0C,
                *(const float *)0x01DCFF10, *(const float *)0x01DCFF14,
                *(const int32_t *)0x01DCFF18, *(const int32_t *)0x01DCFF1C,
                *(const int32_t *)0x01DCFF20, *(const float *)0x01DCFF24,
                *(const float *)0x01DCFF28, *(const float *)0x01DCFF2C,
                *(const float *)0x01DCFF30, *(const float *)0x01DCFF34,
                *(const float *)0x01DCFF38, *(const float *)0x01DCFF3C);
            fprintf(trace_file,
                "%d,%d,%d,%d,%d,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%d,%d,",
                *(const int32_t *)(camera + 0x00), *(const int32_t *)(camera + 0x04),
                *(const int32_t *)(camera + 0x08), *(const int32_t *)(camera + 0x0c),
                *(const int32_t *)(camera + 0x10), f32(camera, 0x16c),
                f32(camera, 0x170), f32(camera, 0x174), f32(camera, 0x178),
                f32(camera, 0x17c), f32(camera, 0x180),
                *(const int32_t *)(camera + 0x184), *(const int32_t *)(camera + 0x188));
            fprintf(trace_file,
                "%.9g,%.9g,%.9g,%d,%d,%d,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%d,%d,%u,%d,%u\n",
                f32(camera, 0x194), f32(camera, 0x198), f32(camera, 0x19c),
                *(const int32_t *)(camera + 0x1a0), *(const int32_t *)(camera + 0x1a4),
                *(const int32_t *)(camera + 0x1a8), f32(camera, 0x1ac),
                f32(camera, 0x1b0), f32(camera, 0x1b4), f32(camera, 0x1b8),
                f32(camera, 0x1bc), f32(camera, 0x1c0), f32(camera, 0x1c4),
                f32(camera, 0x1c8), f32(camera, 0x1cc), f32(camera, 0x1d0),
                slot, *(const int32_t *)(mode + 0x00),
                *(const uint32_t *)(mode + 0x08), *(const int32_t *)(adjust + 0x00),
                *(const uint32_t *)(adjust + 0x08));
        }
    }
    if (tick % 60 == 0) fflush(trace_file);
}

EXPORT void __cdecl OnExit(void) {
    if (trace_file) {
        fclose(trace_file);
        trace_file = 0;
    }
    if (collision_file) {
        fclose(collision_file);
        collision_file = 0;
    }
    if (candidate_file) {
        fclose(candidate_file);
        candidate_file = 0;
    }
    if (precise_file) {
        fclose(precise_file);
        precise_file = 0;
    }
    if (precise_branch_file) {
        fclose(precise_branch_file);
        precise_branch_file = 0;
    }
    if (precise_face_file) {
        fclose(precise_face_file);
        precise_face_file = 0;
    }
    if (precise_record_file) {
        fclose(precise_record_file);
        precise_record_file = 0;
    }
    if (precise_selection_file) {
        fclose(precise_selection_file);
        precise_selection_file = 0;
    }
    if (precise_matrix_file) {
        fclose(precise_matrix_file);
        precise_matrix_file = 0;
    }
}
