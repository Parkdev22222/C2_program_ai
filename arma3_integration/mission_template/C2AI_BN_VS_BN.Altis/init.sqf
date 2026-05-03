/*
 * C2AI :: BN vs BN - init.sqf
 *
 * BLUFOR Alpha [7500,5000] north / Bravo [7500,6500] north
 * OPFOR  Red1  [22000,20000] south / Red2  [22000,21500] south
 */

// ── 1. 상수 ───────────────────────────────────────────────────────
C2AI_APC_PER_CO  = 8;
C2AI_INF_PER_APC = 10;
C2AI_APC_SPACING = 60;

C2AI_START_DELAY = if (isNil "ParamStartDelay") then {60} else {ParamStartDelay};

// ── 2. 유닛 클래스 ────────────────────────────────────────────────
C2AI_B_APC  = "B_APC_Wheeled_01_cannon_F";
C2AI_B_CREW = "B_crew_F";
C2AI_B_INF  = [
    "B_Soldier_SL_F",
    "B_soldier_AR_F",
    "B_Soldier_LAT_F",
    "B_Medic_F",
    "B_Soldier_F",
    "B_Soldier_F",
    "B_Soldier_F",
    "B_Soldier_F",
    "B_Soldier_F",
    "B_Soldier_F"
];

C2AI_O_APC  = "O_APC_Wheeled_02_rcws_F";
C2AI_O_CREW = "O_crew_F";
C2AI_O_INF  = [
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

// ── 3. 중대 스폰 함수 ─────────────────────────────────────────────
C2AI_fnc_spawnCompany = {
    params ["_gid","_side","_center","_facing","_apcClass","_crewClass","_infClasses"];

    private _cx   = _center select 0;
    private _cy   = _center select 1;
    private _sinF = sin _facing;
    private _cosF = cos _facing;

    private _grp = createGroup _side;
    _grp setGroupId [_gid, ""];

    for "_i" from 0 to (C2AI_APC_PER_CO - 1) do {
        private _offset = (_i - (C2AI_APC_PER_CO - 1) / 2) * C2AI_APC_SPACING;
        private _apcX   = _cx + _cosF * _offset;
        private _apcY   = _cy - _sinF * _offset;
        private _apcPos = [_apcX, _apcY, 0];

        private _apc = createVehicle [_apcClass, _apcPos, [], 0, "NONE"];
        _apc setDir _facing;
        _apc setFuel 1;
        _apc setDamage 0;

        private _driver = _grp createUnit [_crewClass, _apcPos, [], 0, "NONE"];
        _driver moveInDriver _apc;

        private _gunner = _grp createUnit [_crewClass, _apcPos, [], 0, "NONE"];
        _gunner moveInGunner _apc;

        for "_j" from 0 to (C2AI_INF_PER_APC - 1) do {
            private _infClass = _infClasses select _j;
            private _row      = _j mod 2;
            private _col      = floor (_j / 2);
            private _backDist = (_row + 1) * 15;
            private _sideDist = (_col - 2) * 5;

            private _infX = _apcX + (-_sinF) * _backDist + _cosF * _sideDist;
            private _infY = _apcY + (-_cosF) * _backDist + (-_sinF) * _sideDist;

            private _inf = _grp createUnit [_infClass, [_infX, _infY, 0], [], 3, "NONE"];
            _inf setDir _facing;
            _inf setSkill ["aimingAccuracy", 0.35];
            _inf setSkill ["aimingShake",    0.35];
            _inf setSkill ["aimingSpeed",    0.50];
            _inf setSkill ["courage",        0.80];
            _inf setSkill ["spotDistance",   0.60];
            _inf setSkill ["general",        0.50];
        };
    };

    _grp setFormation "COLUMN";
    _grp setSpeedMode "NORMAL";
    { _x setBehaviour "AWARE" } forEach units _grp;

    diag_log format ["[C2AI] spawned: %1  units=%2", _gid, count units _grp];
    _grp
};

// ── 4. 서버 전용: 스폰 + 마커 + 기본 웨이포인트 ──────────────────
if (isServer) then {

    C2AI_GRP_ALPHA = ["Alpha", west,  [7500,  5000,  0],   0, C2AI_B_APC, C2AI_B_CREW, C2AI_B_INF] call C2AI_fnc_spawnCompany;
    C2AI_GRP_BRAVO = ["Bravo", west,  [7500,  6500,  0],   0, C2AI_B_APC, C2AI_B_CREW, C2AI_B_INF] call C2AI_fnc_spawnCompany;
    C2AI_GRP_RED1  = ["Red1",  east,  [22000, 20000, 0], 180, C2AI_O_APC, C2AI_O_CREW, C2AI_O_INF] call C2AI_fnc_spawnCompany;
    C2AI_GRP_RED2  = ["Red2",  east,  [22000, 21500, 0], 180, C2AI_O_APC, C2AI_O_CREW, C2AI_O_INF] call C2AI_fnc_spawnCompany;

    diag_log format ["[C2AI] total spawn done | BLU=%1 OPF=%2",
        (count units C2AI_GRP_ALPHA) + (count units C2AI_GRP_BRAVO),
        (count units C2AI_GRP_RED1)  + (count units C2AI_GRP_RED2)];

    // ── 마커 ──────────────────────────────────────────────────────
    createMarker ["mk_Alpha",   [7500,  5000]];
    "mk_Alpha"  setMarkerShape "ICON";
    "mk_Alpha"  setMarkerType  "mil_dot";
    "mk_Alpha"  setMarkerColor "ColorBlue";
    "mk_Alpha"  setMarkerText  "Alpha";
    "mk_Alpha"  setMarkerSize  [0.8, 0.8];

    createMarker ["mk_Bravo",   [7500,  6500]];
    "mk_Bravo"  setMarkerShape "ICON";
    "mk_Bravo"  setMarkerType  "mil_dot";
    "mk_Bravo"  setMarkerColor "ColorBlue";
    "mk_Bravo"  setMarkerText  "Bravo";
    "mk_Bravo"  setMarkerSize  [0.8, 0.8];

    createMarker ["mk_Red1",    [22000, 20000]];
    "mk_Red1"   setMarkerShape "ICON";
    "mk_Red1"   setMarkerType  "mil_dot";
    "mk_Red1"   setMarkerColor "ColorRed";
    "mk_Red1"   setMarkerText  "Red1";
    "mk_Red1"   setMarkerSize  [0.8, 0.8];

    createMarker ["mk_Red2",    [22000, 21500]];
    "mk_Red2"   setMarkerShape "ICON";
    "mk_Red2"   setMarkerType  "mil_dot";
    "mk_Red2"   setMarkerColor "ColorRed";
    "mk_Red2"   setMarkerText  "Red2";
    "mk_Red2"   setMarkerSize  [0.8, 0.8];

    createMarker ["mk_contact", [14000, 12000]];
    "mk_contact" setMarkerShape "ELLIPSE";
    "mk_contact" setMarkerType  "Empty";
    "mk_contact" setMarkerColor "ColorYellow";
    "mk_contact" setMarkerText  "Contact Zone";
    "mk_contact" setMarkerSize  [3000, 3000];
    "mk_contact" setMarkerAlpha 0.4;

    // ── 기본 웨이포인트 (C2AI 명령 없을 때 자동 전진) ────────────
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
};

// ── 5. 클라이언트 전용: 플레이어 설정 ────────────────────────────
if (hasInterface) then {
    waitUntil { !isNull player && alive player };

    execVM "c2_ai_reporter.sqf";
    execVM "c2_order_executor.sqf";

    player setPos [7800, 4200, 0];
    player setDir 0;
    player addItem "Binocular";
    player addItem "ItemMap";
    player addItem "ItemCompass";
    player addItem "ItemRadio";

    [] spawn {
        sleep 3;
        hint "C2AI BN vs BN - BLUFOR: Alpha + Bravo | OPFOR: Red1 + Red2";
    };
};

diag_log "[C2AI] init.sqf complete";
