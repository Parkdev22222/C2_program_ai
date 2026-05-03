/*
 * C2AI :: 기계화 보병 대대 vs 대대
 * init.sqf — 미션 초기화 및 유닛 스폰
 *
 * ━━━━ 편제 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 *  BLUFOR 대대
 *    Alpha 중대 (groupId: "Alpha")  — APC 8대, 보병 80명
 *    Bravo 중대 (groupId: "Bravo")  — APC 8대, 보병 80명
 *
 *  OPFOR 대대
 *    Red1  중대 (groupId: "Red1")   — APC 8대, 보병 80명
 *    Red2  중대 (groupId: "Red2")   — APC 8대, 보병 80명
 *
 *  총전력: 양측 384명 AI + 차량 32대
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 *
 *  지도 배치 (Altis ASL 좌표)
 *  BLUFOR Alpha : [7500, 5000]  북향(0°)
 *  BLUFOR Bravo : [7500, 6500]  북향(0°)
 *  OPFOR  Red1  : [22000,20000] 남향(180°)
 *  OPFOR  Red2  : [22000,21500] 남향(180°)
 *  양측 거리: 약 15km
 *
 *  APC 배치: 중심 기준 이동 방향 직각으로 60m 간격 일렬
 *  보병 배치: APC 후방 2열 × 5명 (15m, 30m)
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 */

// ── 1. 편제 상수 ───────────────────────────────────────────────────────
C2AI_APC_PER_CO  = 8;   // 중대당 APC 수
C2AI_INF_PER_APC = 10;  // APC당 보병 수
C2AI_APC_SPACING = 60;  // APC 좌우 간격 (m)

// 전투 개시 지연 (description.ext 파라미터)
C2AI_START_DELAY = if (isNil "ParamStartDelay") then {60} else {ParamStartDelay};

// ── 2. 유닛 클래스 정의 ───────────────────────────────────────────────
// BLUFOR — NATO 편제
C2AI_B_APC  = "B_APC_Wheeled_01_cannon_F";  // Pandur II 20mm IFV
C2AI_B_CREW = "B_crew_F";
C2AI_B_INF  =
[
    "B_Soldier_SL_F",   // 분대장      (1명)
    "B_soldier_AR_F",   // 분대기관총수 (1명)
    "B_Soldier_LAT_F",  // 대전차 사수  (1명)
    "B_Medic_F",        // 의무병       (1명)
    "B_Soldier_F",      // 소총수       (6명)
    "B_Soldier_F",
    "B_Soldier_F",
    "B_Soldier_F",
    "B_Soldier_F",
    "B_Soldier_F"
];

// OPFOR — 동구권 편제
C2AI_O_APC  = "O_APC_Wheeled_02_rcws_F";    // MSE-3 Marid RCWS
C2AI_O_CREW = "O_crew_F";
C2AI_O_INF  =
[
    "O_Soldier_SL_F",
    "O_soldier_AR_F",
    "O_Soldier_LAT_F",
    "O_Medic_F",
    "O_Soldier_F",
    "O_Soldier_F",
    "O_Soldier_F",
    "O_Soldier_F",
    "O_Soldier_F",
    "O_Soldier_F"
];

// ── 3. 중대 스폰 함수 ─────────────────────────────────────────────────
/*
 * C2AI_fnc_spawnCompany
 *
 * Params:
 *   0: string  — groupId ("Alpha", "Bravo", "Red1", "Red2")
 *   1: side    — west / east
 *   2: array   — [cx, cy, 0] 중대 중심 좌표 (x=동, y=북)
 *   3: number  — 진영 방향 °(0=북, 90=동, 180=남, 270=서)
 *   4: string  — APC 클래스명
 *   5: string  — 승무원 클래스명
 *   6: array   — 보병 클래스명 10종
 *
 * Returns: group
 *
 * APC 배치: 이동방향 직각(우측) 60m 간격 일렬
 * 보병 배치: 각 APC 후방 2열(15m, 30m) × 5열(5m 간격)
 */
C2AI_fnc_spawnCompany =
{
    params ["_gid","_side","_center","_facing","_apcClass","_crewClass","_infClasses"];

    private _cx   = _center select 0;
    private _cy   = _center select 1;
    private _sinF = sin _facing;
    private _cosF = cos _facing;

    // 중대 그룹 생성 및 ID 설정
    private _grp = createGroup _side;
    _grp setGroupId [_gid];

    // ── APC 반복 ────────────────────────────────────────────────────
    for "_i" from 0 to (C2AI_APC_PER_CO - 1) do
    {
        // 우측(이동 방향 직각) 방향으로 60m 간격 배치
        // 우측 벡터: (cosF, -sinF)
        private _offset = (_i - (C2AI_APC_PER_CO - 1) / 2) * C2AI_APC_SPACING;
        private _apcX   = _cx + _cosF * _offset;
        private _apcY   = _cy - _sinF * _offset;
        private _apcPos = [_apcX, _apcY, 0];

        // ── APC 차량 스폰 ────────────────────────────────────────
        private _apc = createVehicle [_apcClass, _apcPos, [], 0, "NONE"];
        _apc setDir _facing;
        _apc setFuel 1;
        _apc setDamage 0;
        _apc allowDamage true;

        // ── 승무원 스폰 & 탑승 ──────────────────────────────────
        private _driver = _grp createUnit [_crewClass, _apcPos, [], 0, "NONE"];
        _driver moveInDriver _apc;

        private _gunner = _grp createUnit [_crewClass, _apcPos, [], 0, "NONE"];
        _gunner moveInGunner _apc;

        // ── 보병 스폰 (APC 후방 2열 × 5열) ────────────────────
        // 후방 벡터: (-sinF, -cosF)
        // 우측 벡터: ( cosF, -sinF)
        for "_j" from 0 to (C2AI_INF_PER_APC - 1) do
        {
            private _infClass  = _infClasses select _j;
            private _row       = _j mod 2;         // 0=첫째 열, 1=둘째 열
            private _col       = floor (_j / 2);   // 0-4 (좌→우)
            private _backDist  = (_row + 1) * 15;  // 후방 거리: 15m, 30m
            private _sideDist  = (_col - 2) * 5;   // 좌우: -10~10m

            private _infX = _apcX + (-_sinF) * _backDist + _cosF * _sideDist;
            private _infY = _apcY + (-_cosF) * _backDist + (-_sinF) * _sideDist;

            private _inf = _grp createUnit [_infClass, [_infX, _infY, 0], [], 3, "NONE"];
            _inf setDir _facing;

            // AI 스킬 (보통 수준)
            _inf setSkill ["aimingAccuracy", 0.35];
            _inf setSkill ["aimingShake",    0.35];
            _inf setSkill ["aimingSpeed",    0.50];
            _inf setSkill ["commanding",     0.50];
            _inf setSkill ["courage",        0.80];
            _inf setSkill ["spotDistance",   0.60];
            _inf setSkill ["spotTime",       0.60];
            _inf setSkill ["general",        0.50];
        };
    };

    // 그룹 초기 행동
    _grp setFormation "COLUMN";
    _grp setSpeedMode "NORMAL";
    { _x setBehaviour "AWARE" } forEach units _grp;

    diag_log format ["[C2AI] 중대 스폰: %1  |  유닛=%2  |  side=%3",
        _gid, count units _grp, _side];

    _grp  // 반환
};

// ── 4. 서버 전용: 유닛 스폰 ───────────────────────────────────────────
if (isServer) then
{
    // ── BLUFOR 대대 ───────────────────────────────────────────────
    //   Alpha 중대: 남부 집결지 [7500, 5000], 북향
    C2AI_GRP_ALPHA = [
        "Alpha", west,
        [7500, 5000, 0], 0,
        C2AI_B_APC, C2AI_B_CREW, C2AI_B_INF
    ] call C2AI_fnc_spawnCompany;

    //   Bravo 중대: Alpha 후방 1500m [7500, 6500], 북향 (예비)
    C2AI_GRP_BRAVO = [
        "Bravo", west,
        [7500, 6500, 0], 0,
        C2AI_B_APC, C2AI_B_CREW, C2AI_B_INF
    ] call C2AI_fnc_spawnCompany;

    // ── OPFOR 대대 ────────────────────────────────────────────────
    //   Red1 중대: 북부 집결지 [22000, 20000], 남향
    C2AI_GRP_RED1 = [
        "Red1", east,
        [22000, 20000, 0], 180,
        C2AI_O_APC, C2AI_O_CREW, C2AI_O_INF
    ] call C2AI_fnc_spawnCompany;

    //   Red2 중대: Red1 후방 1500m [22000, 21500], 남향
    C2AI_GRP_RED2 = [
        "Red2", east,
        [22000, 21500, 0], 180,
        C2AI_O_APC, C2AI_O_CREW, C2AI_O_INF
    ] call C2AI_fnc_spawnCompany;

    diag_log format [
        "[C2AI] 전체 스폰 완료 | BLUFOR=%1 | OPFOR=%2",
        (count units C2AI_GRP_ALPHA) + (count units C2AI_GRP_BRAVO),
        (count units C2AI_GRP_RED1)  + (count units C2AI_GRP_RED2)
    ];

    // ── 5. 지도 마커 ──────────────────────────────────────────────
    // BLUFOR 마커
    private _mkA = createMarker ["mk_Alpha", [7500, 5000]];
    _mkA setMarkerShape "ICON";
    _mkA setMarkerType  "mil_dot";
    _mkA setMarkerColor "ColorBlue";
    _mkA setMarkerText  "Alpha 중대";
    _mkA setMarkerSize  [0.8, 0.8];

    private _mkB = createMarker ["mk_Bravo", [7500, 6500]];
    _mkB setMarkerShape "ICON";
    _mkB setMarkerType  "mil_dot";
    _mkB setMarkerColor "ColorBlue";
    _mkB setMarkerText  "Bravo 중대";
    _mkB setMarkerSize  [0.8, 0.8];

    // OPFOR 마커
    private _mkR1 = createMarker ["mk_Red1", [22000, 20000]];
    _mkR1 setMarkerShape "ICON";
    _mkR1 setMarkerType  "mil_dot";
    _mkR1 setMarkerColor "ColorRed";
    _mkR1 setMarkerText  "Red1 중대";
    _mkR1 setMarkerSize  [0.8, 0.8];

    private _mkR2 = createMarker ["mk_Red2", [22000, 21500]];
    _mkR2 setMarkerShape "ICON";
    _mkR2 setMarkerType  "mil_dot";
    _mkR2 setMarkerColor "ColorRed";
    _mkR2 setMarkerText  "Red2 중대";
    _mkR2 setMarkerSize  [0.8, 0.8];

    // 교전 예상 구역 마커
    private _mkCZ = createMarker ["mk_contact", [14000, 12000]];
    _mkCZ setMarkerShape "ELLIPSE";
    _mkCZ setMarkerType  "Empty";
    _mkCZ setMarkerColor "ColorYellow";
    _mkCZ setMarkerText  "예상 교전 구역";
    _mkCZ setMarkerSize  [3000, 3000];
    _mkCZ setMarkerAlpha 0.4;

    // ── 6. 기본 전진 웨이포인트 (C2AI 명령 미수신 시 자동 교전) ──
    // C2AI 에이전트가 명령 발령 전까지 기본 전진 실행
    // StartDelay 후 실행
    [
        C2AI_GRP_ALPHA, C2AI_GRP_BRAVO,
        C2AI_GRP_RED1,  C2AI_GRP_RED2,
        C2AI_START_DELAY
    ] spawn
    {
        params ["_alpha","_bravo","_red1","_red2","_delay"];
        sleep _delay;
        diag_log format ["[C2AI] 전투 개시 — 기본 전진 명령 실행 (%1초 대기 후)", _delay];

        // BLUFOR → 북방 중간 지점으로 전진
        private _fnAddWP = {
            params ["_grp","_pos","_radius","_type"];
            private _wp = _grp addWaypoint [_pos, _radius];
            _wp setWaypointType _type;
            _wp setWaypointSpeed "FULL";
            _wp setWaypointBehaviour "COMBAT";
            _wp setWaypointFormation "WEDGE";
        };

        [C2AI_GRP_ALPHA, [7500,  14000, 0], 200, "MOVE"]   call _fnAddWP;
        [C2AI_GRP_ALPHA, [12000, 14000, 0], 400, "ATTACK"] call _fnAddWP;

        [C2AI_GRP_BRAVO, [7500,  12000, 0], 200, "MOVE"]   call _fnAddWP;
        [C2AI_GRP_BRAVO, [12000, 12000, 0], 400, "ATTACK"] call _fnAddWP;

        // OPFOR → 남방 중간 지점으로 전진
        [C2AI_GRP_RED1, [22000, 14000, 0], 200, "MOVE"]    call _fnAddWP;
        [C2AI_GRP_RED1, [16000, 14000, 0], 400, "ATTACK"]  call _fnAddWP;

        [C2AI_GRP_RED2, [22000, 12500, 0], 200, "MOVE"]    call _fnAddWP;
        [C2AI_GRP_RED2, [16000, 12500, 0], 400, "ATTACK"]  call _fnAddWP;

        diag_log "[C2AI] 기본 전진 웨이포인트 설정 완료";
    };
};

// ── 7. C2AI 시스템 초기화 (모든 클라이언트) ──────────────────────────
// 플레이어가 완전히 로드된 후 실행
waitUntil { !isNull player && alive player };

// 전장 데이터 수집 시작
execVM "c2_ai_reporter.sqf";

// 임무 명령 수신·실행 시작 (relay.py가 생성하는 c2ai_order_N.sqf 감시)
execVM "c2_order_executor.sqf";

// ── 8. 플레이어 관측 설정 ────────────────────────────────────────────
// 플레이어는 BLUFOR 관측 장교 역할
// 생존 유닛 최고 위치로 이동 (지도상 BLUFOR 지휘소 뒤쪽 언덕)
player setPos [7800, 4200, 0];
player setDir 0;

// 관측 장비 지급
player addItem "Binocular";
player addItem "ItemMap";
player addItem "ItemCompass";
player addItem "ItemRadio";

// HUD 안내 메시지
[
    "C2AI 대대 vs 대대 시나리오",
    "<br/>" +
    "BLUFOR: Alpha 중대(APC 8대, 보병 80명) + Bravo 중대(APC 8대, 보병 80명)<br/>" +
    "OPFOR:  Red1  중대(APC 8대, 보병 80명) + Red2  중대(APC 8대, 보병 80명)<br/>" +
    "<br/>" +
    "C2AI reporter: 10초마다 전장 데이터 전송 중...<br/>" +
    "C2AI executor: 임무 명령 수신 대기 중..."
] call
{
    params ["_title","_body"];
    [_title, _body] spawn
    {
        params ["_t","_b"];
        sleep 3;
        titleText [_t, "PLAIN DOWN", 1];
        sleep 2;
        hint _b;
    };
};

diag_log "[C2AI] init.sqf 완료";
