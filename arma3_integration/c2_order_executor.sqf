/*
 * C2 AI Order Executor - ARMA3 임무 명령 자동 실행기
 *
 * [설치 방법]
 * 1. 이 파일을 미션 폴더에 복사
 * 2. init.sqf 또는 description.ext에서 호출:
 *      execVM "c2_order_executor.sqf";
 *
 * [동작 방식]
 * - relay.py가 Colab에서 수신한 임무 명령을 미션 폴더에
 *   c2ai_order_1.sqf, c2ai_order_2.sqf ... 형태로 저장
 * - 이 스크립트가 3초마다 다음 번호의 파일 존재 여부를 확인
 * - 파일 발견 시 execVM으로 즉시 실행
 *
 * [중요] 각 중대의 groupId가 SQF 명령의 company_id와 일치해야 합니다.
 *   예) 에이전트가 company_id: "Alpha" 로 명령 발행
 *       → ARMA3에서 해당 그룹의 groupId가 "Alpha"이어야 함
 *   그룹 ID 확인:  hint str (groupId (group player));
 *   그룹 ID 변경:  [group someUnit, "Alpha"] call BIS_fnc_setGroupID;
 *
 * [기계화 보병 대대 그룹 ID 권장 명명 규칙]
 *   BLUFOR: "Alpha", "Bravo", "Charlie", "Delta", "HQ"
 *   OPFOR:  "Red1", "Red2", "Red3", "Red4", "RedHQ"
 */

// ── 설정 ─────────────────────────────────────────────────────────
C2AI_ORDER_POLL_INTERVAL = 3;   // 명령 파일 확인 주기 (초)
C2AI_ORDER_MAX_SEQ       = 9999; // 최대 명령 번호

// ── 초기화 ────────────────────────────────────────────────────────
if (!isNil "C2AI_ORDER_EXEC_RUNNING") exitWith {
    diag_log "[C2AI Executor] 이미 실행 중 — 중복 실행 방지";
};
C2AI_ORDER_EXEC_RUNNING = true;
C2AI_LAST_ORDER_SEQ     = 0;

diag_log "[C2AI Executor] 시작 — 임무 명령 대기 중";

// ── 메인 폴링 루프 ───────────────────────────────────────────────
while { C2AI_ORDER_EXEC_RUNNING } do {

    private _nextSeq  = C2AI_LAST_ORDER_SEQ + 1;
    private _fileName = format ["c2ai_order_%1.sqf", _nextSeq];

    if (fileExists _fileName) then {
        diag_log format ["[C2AI Executor] 명령 파일 감지: %1  (seq=%2)", _fileName, _nextSeq];

        // 명령 SQF 실행
        execVM _fileName;
        C2AI_LAST_ORDER_SEQ = _nextSeq;

        diag_log format ["[C2AI Executor] 명령 #%1 실행 시작", _nextSeq];

        // 연속 명령이 있을 경우 즉시 다음 확인 (짧은 대기)
        sleep 1;
    } else {
        sleep C2AI_ORDER_POLL_INTERVAL;
    };

    // 최대 번호 초과 시 루프 종료 (비정상 상황 방지)
    if (_nextSeq > C2AI_ORDER_MAX_SEQ) exitWith {
        diag_log "[C2AI Executor] 최대 명령 번호 초과 — 종료";
        C2AI_ORDER_EXEC_RUNNING = false;
    };
};
