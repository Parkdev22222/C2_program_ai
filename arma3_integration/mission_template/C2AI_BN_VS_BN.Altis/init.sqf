/*
 * C2AI :: BN vs BN - init.sqf
 * 모든 머신(서버+클라이언트)에서 실행
 * 서버 전용 스폰은 initServer.sqf 에서 처리
 */

// ── 상수 ──────────────────────────────────────────────────────────
C2AI_APC_PER_CO  = 8;
C2AI_INF_PER_APC = 10;
C2AI_APC_SPACING = 60;

C2AI_START_DELAY = if (isNil "ParamStartDelay") then {60} else {ParamStartDelay};

// ── BLUFOR 클래스 ─────────────────────────────────────────────────
C2AI_B_APC  = "B_APC_Wheeled_01_cannon_F";
C2AI_B_CREW = "B_crew_F";
C2AI_B_INF  = [
    "B_Soldier_SL_F",
    "B_Soldier_AR_F",
    "B_Soldier_LAT_F",
    "B_Medic_F",
    "B_Soldier_F",
    "B_Soldier_F",
    "B_Soldier_F",
    "B_Soldier_F",
    "B_Soldier_F",
    "B_Soldier_F"
];

// ── OPFOR 클래스 ──────────────────────────────────────────────────
C2AI_O_APC  = "O_APC_Wheeled_02_rcws_F";
C2AI_O_CREW = "O_crew_F";
C2AI_O_INF  = [
    "O_Soldier_SL_F",
    "O_Soldier_AR_F",
    "O_Soldier_LAT_F",
    "O_Medic_F",
    "O_Soldier_F",
    "O_Soldier_F",
    "O_Soldier_F",
    "O_Soldier_F",
    "O_Soldier_F",
    "O_Soldier_F"
];

// ── 중대 스폰 함수 (initServer.sqf 에서 호출) ─────────────────────
C2AI_fnc_spawnCompany = {
    params ["_gid","_side","_center","_facing","_apcClass","_crewClass","_infClasses"];

    private _cx   = _center select 0;
    private _cy   = _center select 1;
    private _sinF = sin _facing;
    private _cosF = cos _facing;

    private _grp = createGroup _side;
    _grp setGroupId [_gid];

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
            _inf setSkill ["courage",        0.80];
            _inf setSkill ["spotDistance",   0.60];
            _inf setSkill ["general",        0.50];
        };
    };

    _grp setFormation "COLUMN";
    _grp setSpeedMode "NORMAL";
    { _x setBehaviour "AWARE" } forEach units _grp;

    diag_log format ["[C2AI] spawned %1 | units=%2", _gid, count units _grp];
    _grp
};

// ── 클라이언트 전용: 플레이어 설정 ───────────────────────────────
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
        hint "C2AI BN vs BN | BLUFOR: Alpha + Bravo | OPFOR: Red1 + Red2";
    };
};

diag_log "[C2AI] init.sqf done";
