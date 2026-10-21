-- Parameters of the battery and inverter wear model on the Health page (web/health.py).
-- Typical values for LFP batteries and hybrid inverters; the owner edits them on /health.
INSERT INTO param (key, valid_from, value, unit, note) VALUES
 ('health_batt_rated_cycles', '2000-01-01', 6000, 'цикъла',
  'цикли до края на живота при 25 °C, 0.5C и 90% DoD (от каталога или гаранцията на батерията)'),
 ('health_batt_eol_pct', '2000-01-01', 70, '%', 'край на живота: остатъчен капацитет'),
 ('health_batt_calendar_fade_pct', '2000-01-01', 1.0, '%/год',
  'календарно стареене при 25 °C и среден SOC след първите месеци (типично за LFP); удвоява се на всеки 10 °C отгоре'),
 ('health_batt_high_soc_factor', '2000-01-01', 0.6, '',
  'с колко повече старее батерията, докато SOC е ≥ 90% (0.6 = с 60% по-бързо)'),
 ('health_batt_break_in_months', '2000-01-01', 18, 'месеца',
  'първите месеци LFP губи капацитет по-бързо (≈ √време), после почти линейно; 0 = линейно от началото'),
 ('health_batt_reserve_pct', '2000-01-01', 3, '%',
  'с колко реалният капацитет на новите модули е над номиналния (оценка); докато резервът не се изчерпи, BMS показва 100%'),
 ('health_batt_app_soh_pct', '2000-01-01', NULL, '%',
  'SOH, което показва приложението Deye (въвежда се ръчно; не идва през API)'),
 ('health_inv_design_life_years', '2000-01-01', 10, 'години', 'проектен живот на инвертора при референтната температура'),
 ('health_inv_ref_temp_c', '2000-01-01', 45, '°C', 'температура на AC радиатора, приета за нормална');
