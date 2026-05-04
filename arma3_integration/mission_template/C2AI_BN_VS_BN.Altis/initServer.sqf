/*
 * C2AI :: BN vs BN - initServer.sqf
 * 서버(또는 로컬 호스트)에서만 자동 실행 — isServer 조건 불필요
 *
 * BLUFOR Alpha [7500,5000]  북향(0)
 * BLUFOR Bravo [7500,6500]  북향(0)
 * OPFOR  Red1  [22000,20000] 남향(180)
 * OPFOR  Red2  [22000,21500] 남향(180)
 */

// init.sqf의 상수·함수가 먼저 로드될 때까지 대기
waitUntil { !isNil "C2AI_fnc_spawnCompany" };

// ── 스폰 ──────────────────────────────────────────────────────────
C2AI_GRP_ALPHA = ["Alpha", west, [7500,  5000,  0],   0, C2AI_B_APC, C2AI_B_CREW, C2AI_B_INF] call C2AI_fnc_spawnCompany;
C2AI_GRP_BRAVO = ["Bravo", west, [7500,  6500,  0],   0, C2AI_B_APC, C2AI_B_CREW, C2AI_B_INF] call C2AI_fnc_spawnCompany;
C2AI_GRP_RED1  = ["Red1",  east, [22000, 20000, 0], 180, C2AI_O_APC, C2AI_O_CREW, C2AI_O_INF] call C2AI_fnc_spawnCompany;
C2AI_GRP_RED2  = ["Red2",  east, [22000, 21500, 0], 180, C2AI_O_APC, C2AI_O_CREW, C2AI_O_INF] call C2AI_fnc_spawnCompany;

diag_log format ["[C2AI] spawn done | BLU=%1 OPF=%2",
    (count units C2AI_GRP_ALPHA) + (count units C2AI_GRP_BRAVO),
    (count units C2AI_GRP_RED1)  + (count units C2AI_GRP_RED2)];

// ── 마커 ──────────────────────────────────────────────────────────
createMarker ["mk_Alpha",   [7500,  5000]];
"mk_Alpha"   setMarkerShape "ICON";
"mk_Alpha"   setMarkerType  "mil_dot";
"mk_Alpha"   setMarkerColor "ColorBlue";
"mk_Alpha"   setMarkerText  "Alpha";

createMarker ["mk_Bravo",   [7500,  6500]];
"mk_Bravo"   setMarkerShape "ICON";
"mk_Bravo"   setMarkerType  "mil_dot";
"mk_Bravo"   setMarkerColor "ColorBlue";
"mk_Bravo"   setMarkerText  "Bravo";

createMarker ["mk_Red1",    [22000, 20000]];
"mk_Red1"    setMarkerShape "ICON";
"mk_Red1"    setMarkerType  "mil_dot";
"mk_Red1"    setMarkerColor "ColorRed";
"mk_Red1"    setMarkerText  "Red1";

createMarker ["mk_Red2",    [22000, 21500]];
"mk_Red2"    setMarkerShape "ICON";
"mk_Red2"    setMarkerType  "mil_dot";
"mk_Red2"    setMarkerColor "ColorRed";
"mk_Red2"    setMarkerText  "Red2";

createMarker ["mk_contact", [14000, 12000]];
"mk_contact" setMarkerShape "ELLIPSE";
"mk_contact" setMarkerType  "Empty";
"mk_contact" setMarkerColor "ColorYellow";
"mk_contact" setMarkerText  "Contact Zone";
"mk_contact" setMarkerSize  [3000, 3000];
"mk_contact" setMarkerAlpha 0.4;

// ── 기본 전진 웨이포인트 (C2AI 명령 미수신 시 자동 교전) ──────────
[C2AI_GRP_ALPHA, C2AI_GRP_BRAVO, C2AI_GRP_RED1, C2AI_GRP_RED2, C2AI_START_DELAY] spawn {
    params ["_alpha","_bravo","_red1","_red2","_delay"];
    sleep _delay;

    private _fnAddWP = {
        params ["_grp","_pos","_radius","_type"];
        private _wp = _grp addWaypoint [_pos, _radius];
        _wp setWaypointType      _type;
        _wp setWaypointSpeed     "FULL";
        _wp setWaypointBehaviour "COMBAT";
        _wp setWaypointFormation "WEDGE";
    };

    [_alpha, [7500,  14000, 0], 200, "MOVE"]   call _fnAddWP;
    [_alpha, [12000, 14000, 0], 400, "ATTACK"] call _fnAddWP;
    [_bravo, [7500,  12000, 0], 200, "MOVE"]   call _fnAddWP;
    [_bravo, [12000, 12000, 0], 400, "ATTACK"] call _fnAddWP;
    [_red1,  [22000, 14000, 0], 200, "MOVE"]   call _fnAddWP;
    [_red1,  [16000, 14000, 0], 400, "ATTACK"] call _fnAddWP;
    [_red2,  [22000, 12500, 0], 200, "MOVE"]   call _fnAddWP;
    [_red2,  [16000, 12500, 0], 400, "ATTACK"] call _fnAddWP;

    diag_log "[C2AI] default waypoints set";
};
