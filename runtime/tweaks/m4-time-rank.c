/* m4-time-rank: Mission 4, the mission with a time limit, is ranked by the clear time instead of by
 * the score; a Perfect still gives an A, and every other mission is ranked as the game ranks it
 * (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_44f670
 *
 * Where the rank comes from, read from the unpacked image. 0x0044F670 (AwardRankForScore, ECX = the
 * results task's data) is called once per stage clear, from one place: the arm 0x0044F8C3 of the
 * results task 0x0044F7C0 (CalcTotalScore; the lifted arm links sub_44f670 directly), `mov ecx,esi;
 * call 0x0044F670`. It does, in order:
 *
 *   [ecx+0xC] = the score (0x0174B050); [ecx+8] = the time bonus, from the clear time in seconds,
 *   TimerMinutes (0x0174AFDB) * 60 + TimerSeconds (0x0174AFDC); [ecx+3] = 1 (a Perfect) when
 *   RingCount (0x0174B028) >= 0x0174B02E, else 0; the run's rings added to the total at 0x0174B05C.
 *   Then the rank, 0 (E) to 4 (A): a Perfect is 4; otherwise (score + time bonus) / 100 against the
 *   four thresholds of CurrentLevel (int16 0x01934B70) in the score table of the mission
 *   (MissionNum 0x0174AFE3: 0x0173AFA0 for mission 5, 0x0173AE70 for mission 4, 0x0173AD40 for the
 *   rest; 10-byte rows, level and four words, ended by level 0x5B; a level not in it is E). The
 *   rank is stored in three places: [ecx+2], CurrentLevelRank (0x0174B001) and [[0x01A5A230]+3].
 *
 * The results screen draws the rank letter from [ecx+2] (0x00450372 in 0x0044FD90, and 0x00450D5A
 * in 0x00450B10), CurrentLevelRank chooses the rank's voice line (0x0044FC6E, through the voice
 * player 0x00443130), and the save keeps it: 0x004457CB stores CurrentLevelRank + 1 for the mission
 * when that is more than the saved byte, so a save only ever keeps its best rank.
 *
 * The wrap reads ECX on entry, calls the lifted body (every side effect above happens as it
 * does with the tweak off), and then, when MissionNum is 3 (mission 4) and [ecx+3] is 0 (not a
 * Perfect, which keeps its A), ranks the clear by its time and writes that rank to the same three
 * places. The clear time is counted as 0x004506E4 counts it for the game's own time-ranked results
 * (bosses and races, 0x004506C0): minutes * 60 + seconds, plus 1 when the frames byte (0x0174AFDD)
 * is past 0. The limit is the game's own: Mission4ClearTimes (0x0173B318), 3-byte rows (level,
 * minutes, seconds) ended by level 0x5B, read from guest memory, the table 0x00451F98 reads when it
 * starts mission 4's time limit (the task 0x00451DF0 fails the mission when the timer, which counts
 * up, reaches it). As 0x00451FC1 does, the Cannon's Core levels 0x22 to 0x26 use the row for 0x24.
 * The time table is OpenSA2's own, in tenths of that limit (TIME_PERCENT): A at most 60% of the
 * limit, B 70%, C 80%, D 90%, E slower; each threshold is passed when the time is at or under it,
 * as in 0x00450670's loop. Metal Harbor's limit is 2:00, so an A is 1:12 or faster.
 *
 * Off is the lifted code alone: the wrap's first statement is the sa2_tweak_on test, and off it
 * only calls __real_sub_44f670. On, every mission but 4, and a Perfect, end with what the lifted body
 * left; nothing but the three rank bytes is written, and only in mission 4.
 *
 * Each mission 4 result is said in its own line: "sa2 tweaks: mission 4 ranked by time: level 10
 * cleared in 0:50 of 2:00: rank A (the score gave E)". A level with no row in Mission4ClearTimes, a
 * Perfect, and a guest read or write that fails are said too, and keep the score's rank.
 *
 * Risks. The rank the save keeps for mission 4 differs from the game's: an A by time is an A in the
 * save (and counts toward the emblems and the all-A unlocks), and a slow clear with a high score
 * gets a lower rank, which the save ignores when it holds a better one. The results screen still
 * counts the score and the time bonus up as the game does; only the letter and the voice follow
 * the time rank (the counters, 0x0044FD90 and 0x00450B10, are left alone). The time table is not
 * the game's, which ranks mission 4 by score and has no time table for it. A write that fails
 * after another succeeded leaves the copies apart; the line names it.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define M4_MISSION_NUM      0x0174AFE3u   /* int8, 0-based: 3 is mission 4 */
#define M4_CURRENT_LEVEL    0x01934B70u   /* int16 */
#define M4_TIMER_MINUTES    0x0174AFDBu   /* int8 */
#define M4_TIMER_SECONDS    0x0174AFDCu   /* int8 */
#define M4_TIMER_FRAMES     0x0174AFDDu   /* int8 */
#define M4_CLEAR_TIMES      0x0173B318u   /* Mission4ClearTimes: {level, minutes, seconds}..., 0x5B */
#define M4_LEVEL_RANK       0x0174B001u   /* CurrentLevelRank, int8 */
#define M4_HUD_POINTER      0x01A5A230u   /* the rank's third copy is at [this] + 3 */
#define M4_TABLE_END        0x5Bu
#define M4_TABLE_ROWS       128u          /* a bound on the walk; the game's table has 30 rows */

/* The clear time, in percent of the limit, at or under which each step up from E is passed. */
static const unsigned TIME_PERCENT[4] = {90, 80, 70, 60};
static const char RANK_LETTER[5] = {'E', 'D', 'C', 'B', 'A'};

static int peek8(uint32_t va, int8_t *out) { return sa2_peek(va, 1, out) == SA2_OK; }

static char rank_letter(int8_t rank) { return rank >= 0 && rank <= 4 ? RANK_LETTER[rank] : '?'; }

/* The level's limit in seconds from Mission4ClearTimes, or -1 when it has no row; -2 when the
 * table does not read. */
static int m4_limit(int16_t level) {
    const uint8_t key = level >= 0x22 && level <= 0x26 ? 0x24u : (uint8_t)level;
    for (uint32_t i = 0; i < M4_TABLE_ROWS; i++) {
        uint8_t row[3];
        if (sa2_peek(M4_CLEAR_TIMES + 3u * i, 1, row) != SA2_OK) return -2;
        if (row[0] == M4_TABLE_END) return -1;
        if (row[0] != key) continue;
        if (sa2_peek(M4_CLEAR_TIMES + 3u * i, 3, row) != SA2_OK) return -2;
        return row[1] * 60 + row[2];
    }
    return -1;
}

void *__real_sub_44f670(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_44f670(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_M4_TIME_RANK)) return __real_sub_44f670(state, pc, memory);
    const uint32_t results = sa2_rt_get_gpr(state, SA2_ECX);
    void *ret = __real_sub_44f670(state, pc, memory);
    int8_t mission = 0, perfect = 0, score_rank = 0, minutes = 0, seconds = 0, frames = 0;
    int16_t level = 0;
    if (!peek8(M4_MISSION_NUM, &mission)) {
        fprintf(stderr, "sa2 tweaks: mission 4 ranked by time: MissionNum (0x%08X) does not read, so "
                "the clear keeps the score's rank\n", M4_MISSION_NUM);
        return ret;
    }
    if (mission != 3) return ret;
    if (!peek8(results + 3u, &perfect) || !peek8(results + 2u, &score_rank) ||
        sa2_peek(M4_CURRENT_LEVEL, 2, &level) != SA2_OK || !peek8(M4_TIMER_MINUTES, &minutes) ||
        !peek8(M4_TIMER_SECONDS, &seconds) || !peek8(M4_TIMER_FRAMES, &frames)) {
        fprintf(stderr, "sa2 tweaks: mission 4 ranked by time: the results (0x%08X), the level or the "
                "timer does not read, so the clear keeps the score's rank\n", results);
        return ret;
    }
    if (perfect) {
        fprintf(stderr, "sa2 tweaks: mission 4 ranked by time: level %d: a Perfect keeps its %c\n", level,
                rank_letter(score_rank));
        return ret;
    }
    const int limit = m4_limit(level);
    if (limit < 0) {
        fprintf(stderr, "sa2 tweaks: mission 4 ranked by time: level %d %s, so the clear keeps the "
                "score's rank (%c)\n", level, limit == -1
                ? "has no row in Mission4ClearTimes (0x0173B318)" : "'s row in Mission4ClearTimes "
                "(0x0173B318) does not read", rank_letter(score_rank));
        return ret;
    }
    const int time = minutes * 60 + seconds + (frames > 0);
    int8_t rank = 0;
    while (rank < 4 && time * 100 <= limit * (int)TIME_PERCENT[rank]) rank++;
    uint32_t hud = 0;
    if (sa2_poke(results + 2u, 1, &rank) != SA2_OK || sa2_poke(M4_LEVEL_RANK, 1, &rank) != SA2_OK ||
        sa2_peek(M4_HUD_POINTER, 4, &hud) != SA2_OK || sa2_poke(hud + 3u, 1, &rank) != SA2_OK) {
        fprintf(stderr, "sa2 tweaks: mission 4 ranked by time: level %d: rank %c by time not written in "
                "full (the results at 0x%08X, CurrentLevelRank, [0x%08X]+3 = 0x%08X)\n", level,
                rank_letter(rank), results, M4_HUD_POINTER, hud + 3u);
        return ret;
    }
    fprintf(stderr, "sa2 tweaks: mission 4 ranked by time: level %d cleared in %d:%02d of %d:%02d: rank %c "
            "(the score gave %c)\n", level, time / 60, time % 60, limit / 60, limit % 60, rank_letter(rank),
            rank_letter(score_rank));
    return ret;
}
