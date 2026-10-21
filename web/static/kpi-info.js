/* What each indicator tile shows, for the "i" button on the tile (PV.tiles).
 * Key: the tile label in Bulgarian. Each entry: [what it shows, how it is counted, what a good value is]
 * in Bulgarian and in English; an empty string leaves the line out.
 */
"use strict";

window.KPI_INFO = {
  // ------------------------------------------------------------ Обзор
  "PV производство": {
    bg: ["Енергията, която панелите (покрив и навес) са произвели за избрания период.", "Сума от мощността на панелите по 5-минутни интервали от Deye.", "Сравни със „Производство спрямо PVGIS“ на страница Цели."],
    en: ["Energy produced by the panels (roof and carport) in the selected period.", "Sum of the panel power over 5-minute intervals from Deye.", "Compare with “Production vs. PVGIS” on the Goals page."],
  },
  "Консумация на дома": {
    bg: ["Целият ток, който домът е изразходвал, независимо откъде е дошъл: панели, батерия или мрежа.", "Сума от товара по 5-минутни интервали; за декември–март е добавен и токът, който електромерът отчита, а Deye не вижда.", "Нощното зареждане на Теслата е голяма част от нея."],
    en: ["All electricity the home used, wherever it came from: panels, battery or grid.", "Sum of the load over 5-minute intervals; for December–March the energy the utility meter saw but Deye did not is added.", "Night charging of the Tesla is a large part of it."],
  },
  "Импорт от мрежата": {
    bg: ["Токът, купен от мрежата (Електрохолд).", "По Deye; там, където има фактура, се ползва електромерът.", "Колкото по-малко, толкова по-добре."],
    en: ["Electricity bought from the grid (Electrohold).", "From Deye; where an invoice exists, the utility meter is used.", "The lower, the better."],
  },
  "Заряд на батерията": {
    bg: ["Енергията, вкарана в батерията, предимно от излишъка на панелите.", "Сума от мощността на заряд по 5-минутни интервали.", ""],
    en: ["Energy put into the battery, mostly from the panels' surplus.", "Sum of the charging power over 5-minute intervals.", ""],
  },
  "Разряд на батерията": {
    bg: ["Енергията, която батерията е дала на дома, най-вече вечер и нощем.", "Сума от мощността на разряд по 5-минутни интервали.", "Колкото повече, толкова по-малко ток се купува нощем."],
    en: ["Energy the battery gave to the home, mostly in the evening and at night.", "Sum of the discharging power over 5-minute intervals.", "The more, the less electricity is bought at night."],
  },
  "Нетна полза за деня": {
    bg: ["Колко пари е спестила системата за избрания ден.", "Сметката, ако нямаше PV, минус реалната сметка, по тарифите с ДДС за всяка зона (ден/нощ).", "Над 0 € значи, че системата печели."],
    en: ["How much money the system saved on the selected day.", "The bill without PV minus the real bill, at the day/night tariffs incl. VAT.", "Above 0 € means the system is paying off."],
  },

  // ------------------------------------------------------------ Производство
  "Добив на kWp (специфичен)": {
    bg: ["Колко kWh е дал всеки киловат инсталирана мощност. Така периоди с различен брой панели се сравняват честно.", "PV производство / инсталирани kWp за всеки ден (21,08 kWp от 09.07.2026).", "В София типично ~1 300–1 400 kWh/kWp за година, ~5–6 на ден през лятото."],
    en: ["How many kWh each installed kilowatt produced, so periods with a different number of panels compare fairly.", "PV production / installed kWp for each day (21.08 kWp since 09.07.2026).", "Typical in Sofia: ~1,300–1,400 kWh/kWp a year, ~5–6 a day in summer."],
  },
  "Пикова PV мощност": {
    bg: ["Най-високата мощност от панелите в периода.", "Максимум от 5-минутните кадри на Deye.", "Над 15 kW не може: това е лимитът на инвертора."],
    en: ["The highest power from the panels in the period.", "Maximum of Deye's 5-minute frames.", "It cannot exceed 15 kW: that is the inverter's limit."],
  },
  "Покрив, специфичен добив": {
    bg: ["Добивът на kWp на южния покрив (PV1, 11,16 kWp).", "Енергия от PV1 / 11,16 kWp; данни по стрингове има от 22.07.2026.", "Покривът трябва да е над навеса, защото няма сянка."],
    en: ["Yield per kWp of the south roof (PV1, 11.16 kWp).", "PV1 energy / 11.16 kWp; per-string data exists since 22.07.2026.", "The roof should beat the carport because it has no shade."],
  },
  "Навес, специфичен добив": {
    bg: ["Добивът на kWp на навеса (PV2, югозапад, 9,92 kWp).", "Енергия от PV2 / 9,92 kWp; данни по стрингове има от 22.07.2026.", "По-нисък от покрива заради следобедната сянка и посоката."],
    en: ["Yield per kWp of the carport (PV2, south-west, 9.92 kWp).", "PV2 energy / 9.92 kWp; per-string data exists since 22.07.2026.", "Lower than the roof because of afternoon shade and orientation."],
  },
  "Инвертор, макс. температура": {
    bg: ["Най-високата температура на радиатора на инвертора (AC страната).", "Максимум от кадрите на Deye; DC температура API-то не дава.", "До ~60 °C е нормално; над 70 °C инверторът намалява мощността."],
    en: ["The highest temperature of the inverter heat sink (AC side).", "Maximum of Deye's frames; the API does not give the DC temperature.", "Up to ~60 °C is normal; above 70 °C the inverter reduces power."],
  },
  "Наличност на данните": {
    bg: ["Каква част от времето има данни от инвертора. Ниска стойност значи прекъсване на връзката, а не непременно спрял инвертор.", "Време с 5-минутни кадри / цялото време в периода.", "Близо до 100%."],
    en: ["Share of the time with data from the inverter. A low value means a lost connection, not necessarily a stopped inverter.", "Time covered by 5-minute frames / the whole period.", "Close to 100%."],
  },
  "Аларми на инвертора": {
    bg: ["Брой аларми на инвертора.", "Колекторът още не чете алармите от Deye, затова е 0.", ""],
    en: ["Number of inverter alarms.", "The collector does not read alarms from Deye yet, so this is 0.", ""],
  },

  // ------------------------------------------------------------ Батерия
  "Заряд мин. / макс. (SOC)": {
    bg: ["Най-ниското и най-високото ниво на заряд на батерията в периода (SOC, % от капацитета). Това е измерено, не настройката.", "Минимум и максимум от 5-минутните кадри; под числото е денят на минимума и дали е под минималния SOC от настройките тогава.", "Минимумът обикновено е колкото настройката; по-ниско значи изключение (напр. 15% сутринта на 09.07.2026, преди да се включат модули 5–8). Редовно 100% ускорява стареенето."],
    en: ["The lowest and highest battery state of charge in the period (SOC, % of capacity). This is measured, not the setting.", "Minimum and maximum of the 5-minute frames; below the number is the day of the minimum and whether it was below the minimum SOC set at the time.", "The minimum is usually the setting; lower means an exception (e.g. 15% on the morning of 09.07.2026, before modules 5–8 were connected). Sitting at 100% often speeds up ageing."],
  },
  "Пълни цикли за периода": {
    bg: ["Колко пъти батерията е разредена изцяло, ако съберем всички частични разреждания.", "Целия разряд в периода / капацитета на целия пакет към края на периода (40,96 kWh от 09.07.2026), както в Обзор и Здраве.", "Батерията е за ~6 000 цикъла; ~130 на година е леко натоварване."],
    en: ["How many times the battery was fully discharged, adding up all partial discharges.", "All discharge in the period / capacity of the whole pack at the end of the period (40.96 kWh from 09.07.2026), as on Overview and Health.", "The battery is rated for ~6,000 cycles; ~130 a year is a light load."],
  },
  "Отдадено / заредено": {
    bg: ["Енергията, която батерията е отдала, и тази, която е получила.", "Суми по 5-минутни интервали.", ""],
    en: ["Energy the battery gave out and energy it took in.", "Sums over 5-minute intervals.", ""],
  },
  "Ефективност на батерията": {
    bg: ["Колко от заредената енергия батерията връща обратно; останалото са загуби при зареждане и разреждане.", "Разряд / заряд за периода. За кратки периоди се влияе от разликата в заряда в началото и в края.", "Над 90% е добре за LFP батерия."],
    en: ["How much of the charged energy the battery gives back; the rest is lost in charging and discharging.", "Discharge / charge for the period. Over short periods it is affected by the difference in charge at the start and end.", "Above 90% is good for an LFP battery."],
  },
  "Време на пълна батерия": {
    bg: ["Колко часа на ден батерията стои на 100%. Тогава излишъкът от панелите се губи (нулев износ).", "Средно на ден, време със SOC ≥ 99%.", "По-малко е по-добре за батерията и значи по-малко загубен ток."],
    en: ["Hours a day the battery sits at 100%. Then the panels' surplus is lost (zero export).", "Average per day, time with SOC ≥ 99%.", "Less is better for the battery and means less wasted energy."],
  },
  "Време на минимума": {
    bg: ["Колко часа на ден батерията е изтощена: на минималния SOC от настройките или под него.", "Средно на ден с данни за SOC, време със SOC ≤ минималния SOC, валиден за всеки ден (Настройки → Батерия); под числото са настройките за периода.", "Малко часа: батерията стига за нощта. Много часа: домът тече от мрежата, по-голям пакет или по-малко нощен товар биха помогнали."],
    en: ["Hours a day the battery is empty: at the minimum SOC from the settings or below it.", "Average per day with SOC data, time with SOC ≤ the minimum SOC valid on each day (Settings → Battery); below the number are the settings for the period.", "Few hours: the battery lasts the night. Many hours: the home runs from the grid, a bigger pack or less night load would help."],
  },
  "Батерията се пълни около": {
    bg: ["Типичният час, в който батерията стига 100%. След него излишъкът от панелите се губи.", "Медиана на първия час със SOC ≥ 99%, само в дните, в които се е напълнила.", "Колкото по-късно, толкова по-малко се губи."],
    en: ["The typical time the battery reaches 100%. After that the panels' surplus is lost.", "Median of the first time with SOC ≥ 99%, only on days it filled up.", "The later, the less is lost."],
  },
  "Нощта от батерията": {
    bg: ["Каква част от нощния ток идва от батерията, а не от мрежата.", "Разряд в нощната тарифа / консумация в нощната тарифа.", "Целта е 80%; Теслата нощем я сваля."],
    en: ["Share of night-time electricity that comes from the battery rather than the grid.", "Discharge in the night tariff / consumption in the night tariff.", "The goal is 80%; the Tesla charging at night pulls it down."],
  },
  "Макс. температура на батерията": {
    bg: ["Най-високата температура на батерията; под нея са градусо-часовете над 35 °C.", "Максимум от кадрите на Deye (данни от 22.07.2026).", "Под 35 °C; над това батерията старее по-бързо."],
    en: ["The highest battery temperature; below it, the degree-hours above 35 °C.", "Maximum of Deye's frames (data since 22.07.2026).", "Below 35 °C; above that the battery ages faster."],
  },

  // ------------------------------------------------------------ Потребление и мрежа
  "Внос от мрежата": {
    bg: ["Токът, купен от мрежата за периода, и най-високата мощност на вноса.", "По Deye; където има фактура, по електромера.", "Колкото по-малко, толкова по-добре."],
    en: ["Electricity bought from the grid in the period, and the highest import power.", "From Deye; where an invoice exists, from the utility meter.", "The lower, the better."],
  },
  "Внос дневна / нощна тарифа": {
    bg: ["Купеният ток, разделен по тарифи. Нощният е по-евтин.", "Нощна тарифа 23–07 ч. април–октомври и 22–06 ч. ноември–март.", "По-добре повече да е нощен, отколкото дневен."],
    en: ["Bought electricity split by tariff. Night is cheaper.", "Night tariff 23–07 h April–October and 22–06 h November–March.", "Better more at night than during the day."],
  },
  "Износ към мрежата": {
    bg: ["Токът, отдаден към мрежата.", "Инверторът е на нулев износ, затова е почти 0; излишъкът се губи.", "При договор за продажба би носил пари."],
    en: ["Electricity sent to the grid.", "The inverter is set to zero export, so it is almost 0; the surplus is lost.", "With a sales contract it would earn money."],
  },
  "Ползвано направо от панелите": {
    bg: ["Токът от панелите, изразходван от дома веднага, без да минава през батерията.", "PV − заряд от PV − износ, по 5-минутни интервали.", "Това е най-евтиният ток: няма загуби в батерията."],
    en: ["Panel electricity the home used straight away, without going through the battery.", "PV − charging from PV − export, per 5-minute interval.", "This is the cheapest electricity: no battery losses."],
  },
  "Самозадоволяване (без мрежа)": {
    bg: ["Каква част от консумацията на дома е покрита без мрежата, от панелите и батерията.", "(консумация − внос) / консумация.", "Целта е 75%."],
    en: ["Share of the home's consumption covered without the grid, by the panels and battery.", "(consumption − import) / consumption.", "The goal is 75%."],
  },
  "Самопотребление (ползвано у дома)": {
    bg: ["Каква част от произведения ток е използвана у дома (направо или през батерията), а не изнесена.", "(PV − износ) / PV. При нулев износ е почти 100%; загубеното заради пълна батерия не се вижда тук, а в „Пропусната енергия“.", ""],
    en: ["Share of produced electricity used at home (directly or via the battery) rather than exported.", "(PV − export) / PV. With zero export it is almost 100%; energy lost because the battery is full shows in “Missed energy”, not here.", ""],
  },
  "Нощно покритие от батерията": {
    bg: ["Каква част от нощния ток идва от батерията, а не от мрежата.", "Разряд в нощната тарифа / консумация в нощната тарифа.", "Целта е 80%; Теслата нощем я сваля."],
    en: ["Share of night-time electricity that comes from the battery rather than the grid.", "Discharge in the night tariff / consumption in the night tariff.", "The goal is 80%; the Tesla charging at night pulls it down."],
  },
  "Заряд на батерията от мрежата": {
    bg: ["Токът от мрежата, вкаран в батерията.", "Заряд над PV мощността в същите 5 минути.", "Нормално е близо до 0, освен ако не е настроено зимно зареждане на нощна тарифа."],
    en: ["Grid electricity put into the battery.", "Charging above PV power in the same 5 minutes.", "Normally close to 0, unless winter charging at the night tariff is set up."],
  },

  // ------------------------------------------------------------ Икономика
  "Вложено в системата": {
    bg: ["Цялата инвестиция (CAPEX) с ДДС; под нея са датите на покупките.", "Сума от записите в Настройки → CAPEX.", ""],
    en: ["The whole investment (CAPEX) incl. VAT; below are the purchase dates.", "Sum of the entries in Settings → CAPEX.", ""],
  },
  "Изплатена част от инвестицията": {
    bg: ["Каква част от вложеното вече е върната от спестения ток.", "Натрупана нетна полза / CAPEX.", "100% значи, че системата е изплатена."],
    en: ["Share of the investment already returned by the electricity saved.", "Cumulative net benefit / CAPEX.", "100% means the system has paid for itself."],
  },
  "Прогнозна окупаемост": {
    bg: ["За колко години от пускането системата ще се изплати.", "CAPEX / годишна нетна полза по сезонния профил, базов сценарий. Звездичката значи, че стойността е прогноза.", "Целта е до 12 години."],
    en: ["How many years from commissioning until the system pays for itself.", "CAPEX / annual net benefit using the seasonal profile, base scenario. The asterisk marks a forecast.", "The goal is 12 years or less."],
  },
  "Прогнозна дата на изплащане": {
    bg: ["Датата, на която натрупаната полза ще стигне вложеното; под нея са другите два сценария.", "По прогнозата за месечната полза, базов сценарий.", ""],
    en: ["The date the cumulative benefit reaches the investment; below are the other two scenarios.", "From the monthly benefit forecast, base scenario.", ""],
  },
  "Сметка преди / след PV": {
    bg: ["Средната месечна сметка за ток преди и след пускането на системата.", "По фактурите на Електрохолд.", ""],
    en: ["Average monthly electricity bill before and after the system was commissioned.", "From the Electrohold invoices.", ""],
  },
  "Нетна полза спрямо бизнес плана": {
    bg: ["Докъде е реалната полза спрямо очакваното в бизнес плана за същия период.", "Нетна полза / план за периода (планът е на страница Цели).", "100% и нагоре значи по план."],
    en: ["How the real benefit compares with what the business plan expected for the same period.", "Net benefit / plan for the period (the plan is on the Goals page).", "100% or more means on plan."],
  },
  "Нетна полза за периода": {
    bg: ["Колко пари е спестила системата за избрания период.", "Сметка, ако нямаше PV − реална сметка + приход от продажба − поддръжка.", ""],
    en: ["How much money the system saved in the selected period.", "Bill without PV − real bill + sales revenue − maintenance.", ""],
  },
  "Сметка, ако нямаше PV": {
    bg: ["Колко би платил за същата консумация без панели и батерия.", "Консумация × цената на тарифата (ден/нощ) с ДДС.", ""],
    en: ["What the same consumption would have cost without panels and battery.", "Consumption × the day/night tariff price incl. VAT.", ""],
  },
  "Реална сметка за ток": {
    bg: ["Колко струва купеният от мрежата ток.", "Внос × цената на тарифата с ДДС; където има фактура, по електромера.", ""],
    en: ["What the electricity bought from the grid cost.", "Import × the tariff price incl. VAT; where an invoice exists, from the utility meter.", ""],
  },
  "Приход от продаден ток": {
    bg: ["Пари от продаден на мрежата ток.", "Износ × изкупна цена; без договор е 0.", ""],
    en: ["Money from electricity sold to the grid.", "Export × buy-back price; without a contract it is 0.", ""],
  },
  "Спестено, дневна / нощна тарифа": {
    bg: ["Спестените пари, разделени по тарифи.", "Разликата между сметката без PV и реалната сметка във всяка тарифа.", ""],
    en: ["Money saved, split by tariff.", "Difference between the bill without PV and the real bill in each tariff.", ""],
  },

  // ------------------------------------------------------------ Цели
  "Производство спрямо PVGIS": {
    bg: ["Колко от очакваното по PVGIS (европейската база за слънчев добив) са произвели панелите тази година.", "PV / PVGIS очакване за масивите, работили в периода.", "Над 90%; ниско значи загуби, сянка или ограничаване при пълна батерия."],
    en: ["How much of the PVGIS expectation (the EU solar yield database) the panels produced this year.", "PV / PVGIS expectation for the arrays that were running in the period.", "Above 90%; low means losses, shade or curtailment when the battery is full."],
  },
  "Пропусната енергия от пускането": {
    bg: ["Колко ток панелите са можели да произведат, но не са, защото батерията е била пълна, а износът е забранен.", "Очаквано (по облъчването от Open-Meteo) − реално PV в интервалите с пълна батерия. Това е оценка.", "При договор за продажба този ток би носил пари."],
    en: ["Electricity the panels could have produced but did not, because the battery was full and export is off.", "Expected (from Open-Meteo irradiance) − real PV in intervals with a full battery. This is an estimate.", "With a sales contract this energy would earn money."],
  },
  "Коефициент на ефективност (PR) от пускането": {
    bg: ["Каква част от слънчевата енергия, паднала върху панелите, системата превръща в ток, спрямо идеалния случай.", "PV / (облъчване в равнината на панелите × kWp). Ограничаването при пълна батерия също го сваля.", "75–85% е типично за здрава система."],
    en: ["How much of the sunlight on the panels the system turns into electricity, compared with the ideal case.", "PV / (irradiance on the panel plane × kWp). Curtailment when the battery is full also lowers it.", "75–85% is typical for a healthy system."],
  },
  "Слънчево облъчване от пускането": {
    bg: ["Колко слънчева енергия е паднала на 1 m² хоризонтална повърхност.", "Open-Meteo, по часове.", "Показва дали периодът е бил слънчев."],
    en: ["Solar energy that fell on 1 m² of horizontal surface.", "Open-Meteo, hourly.", "Shows whether the period was sunny."],
  },
  "Нетна полза спрямо плана": {
    bg: ["Докъде е реалната полза спрямо очакваното в бизнес плана.", "Нетна полза / план за същия период.", "100% и нагоре значи по план."],
    en: ["How the real benefit compares with the business plan.", "Net benefit / plan for the same period.", "100% or more means on plan."],
  },

  // ------------------------------------------------------------ Здраве
  "Цикли до момента": {
    bg: ["Колко пълни цикъла е направила батерията от пускането.", "Целия разряд / капацитета на целия пакет (40,96 kWh).", "Батерията е за ~6 000 цикъла."],
    en: ["How many full cycles the battery has done since commissioning.", "All discharge / capacity of the whole pack (40.96 kWh).", "The battery is rated for ~6,000 cycles."],
  },
  "Здраве на батерията (SOH, модел)": {
    bg: ["Колко от първоначалния капацитет е останал (State of Health). Deye не дава тази стойност, затова е от модел.", "Стареене по време (по-бързо първите 18 месеца), температура, време на 100% и цикли; спрямо номинала с ~3% резерв.", "Над 90% през първите години."],
    en: ["How much of the original capacity is left (State of Health). Deye does not provide it, so it comes from a model.", "Ageing by time (faster in the first 18 months), temperature, time at 100% and cycles; vs. nominal with a ~3% reserve.", "Above 90% in the first years."],
  },
  "Загуба на капацитет, следващите 12 месеца": {
    bg: ["Колко капацитет ще загуби батерията през следващата година при сегашните условия.", "По модела за стареене.", "Под 3% на година."],
    en: ["How much capacity the battery will lose in the next year under current conditions.", "From the ageing model.", "Below 3% a year."],
  },
  "Край на живота на батерията (70%)": {
    bg: ["Годината, в която капацитетът ще падне до 70%, обичайната граница за подмяна.", "По модела за стареене при сегашните условия; отдолу е идеалният случай (25 °C, без 100%).", "Колкото по-късно, толкова по-добре."],
    en: ["The year capacity falls to 70%, the usual replacement threshold.", "From the ageing model under current conditions; below is the ideal case (25 °C, no 100%).", "The later, the better."],
  },
  "Измерен полезен капацитет": {
    bg: ["Колко kWh реално се побират в батерията, изчислено от промяната на заряда.", "Енергия / промяна на SOC при големи разреждания. BMS мащабира SOC към своя капацитет, затова не доказва износване.", ""],
    en: ["How many kWh actually fit in the battery, estimated from the change in charge.", "Energy / change in SOC over large discharges. The BMS scales SOC to its own capacity, so this does not prove wear.", ""],
  },
  "Време на 100%, този месец": {
    bg: ["Колко часа на ден батерията стои на 100% този месец.", "Средно на ден, време със SOC ≥ 99%.", "По-малко е по-добре: дълго на 100% ускорява стареенето."],
    en: ["Hours a day the battery sits at 100% this month.", "Average per day, time with SOC ≥ 99%.", "Less is better: long periods at 100% speed up ageing."],
  },
  "Средна температура на батерията, този месец": {
    bg: ["Средната температура на батерията този месец.", "От кадрите на Deye; преди 22.07.2026 е оценка по външната температура.", "Около 25 °C е идеално; над 30 °C стареенето се ускорява."],
    en: ["Average battery temperature this month.", "From Deye's frames; before 22.07.2026 estimated from outdoor temperature.", "About 25 °C is ideal; above 30 °C ageing speeds up."],
  },
  "Натоварване на батерията": {
    bg: ["Обобщена оценка колко тежки са условията за батерията този месец.", "Комбинира време на 100%, температура, дълбочина на разреждане и мощност на заряд/разряд; 0 е идеално, 100 е много тежко.", "Под 40; от 60 е проблем."],
    en: ["Overall score for how hard conditions are on the battery this month.", "Combines time at 100%, temperature, depth of discharge and charge/discharge power; 0 is ideal, 100 is very hard.", "Below 40; 60 or more is a problem."],
  },
  "Макс. температура на инвертора, този месец": {
    bg: ["Най-високата температура на радиатора на инвертора този месец.", "Максимум от кадрите на Deye (AC страна).", "До ~60 °C е нормално."],
    en: ["The highest inverter heat-sink temperature this month.", "Maximum of Deye's frames (AC side).", "Up to ~60 °C is normal."],
  },
  "Стареене от топлина": {
    bg: ["Колко по-бързо старее инверторът заради температурата, спрямо работа при еталонна температура.", "Правилото „+10 °C удвоява стареенето“ върху измерените температури.", "1× е нормално; над 1,5× помисли за охлаждане."],
    en: ["How much faster the inverter ages because of temperature, compared with the reference temperature.", "The “+10 °C doubles ageing” rule applied to the measured temperatures.", "1× is normal; above 1.5× consider cooling."],
  },
  "Очакван живот на инвертора": {
    bg: ["Колко години се очаква да издържи инверторът при сегашните температури.", "Проектният живот / фактора на стареене от топлина.", ""],
    en: ["How many years the inverter is expected to last at current temperatures.", "Design life / the heat ageing factor.", ""],
  },
  "Време на максимална мощност 15 kW": {
    bg: ["Колко часа този месец инверторът е работил на лимита си от 15 kW.", "Време с AC мощност поне 97% от 15 kW.", "Много часове значи, че панелите могат повече, отколкото инверторът пропуска."],
    en: ["Hours this month the inverter ran at its 15 kW limit.", "Time with AC power at least 97% of 15 kW.", "Many hours means the panels could deliver more than the inverter lets through."],
  },
  "Прекъсвания на мрежата": {
    bg: ["Колко пъти е спирал токът от мрежата.", "Кадри, в които и трите фази са под 50 V (от 22.07.2026).", ""],
    en: ["How many times the grid supply went down.", "Frames where all three phases are below 50 V (since 22.07.2026).", ""],
  },
  "Пренапрежение в мрежата (над 253 V)": {
    bg: ["Колко пъти напрежението в мрежата е било над допустимото (230 V + 10%).", "5-минутни кадри с напрежение над 253 V на някоя фаза (от 22.07.2026).", "Често пренапрежение е проблем на ЕРМ и може да изключва инвертора."],
    en: ["How many times the grid voltage was above the limit (230 V + 10%).", "5-minute frames with voltage above 253 V on any phase (since 22.07.2026).", "Frequent overvoltage is the grid operator's problem and can trip the inverter."],
  },

  // ------------------------------------------------------------ Качество на данните
  "Пълнота на данните": {
    bg: ["Каква част от времето в периода има данни.", "Време, покрито с 5-минутни кадри / цялото време.", "Близо до 100%."],
    en: ["Share of the period covered by data.", "Time covered by 5-minute frames / the whole period.", "Close to 100%."],
  },
  "Интервали с оценено PV": {
    bg: ["5-минутни интервали, в които Deye е показал замръзнала PV мощност и тя е изчислена от баланса.", "PV = товар + заряд − разряд − внос + износ.", "Колкото по-малко, толкова по-добре."],
    en: ["5-minute intervals where Deye showed frozen PV power and it was computed from the balance.", "PV = load + charge − discharge − import + export.", "The fewer, the better."],
  },
  "Последен кадър на станцията": {
    bg: ["Преди колко минути е последният запис от станцията в Deye Cloud.", "Сега − времето на последния кадър.", "Под 15 мин; над 45 мин значи, че нещо е спряло."],
    en: ["How many minutes ago the last station record from Deye Cloud is.", "Now − time of the last frame.", "Below 15 min; above 45 min means something has stopped."],
  },
  "Последен кадър на инвертора": {
    bg: ["Преди колко минути е последният запис от инвертора.", "Сега − времето на последния кадър.", "Под 15 мин; над 45 мин значи, че нещо е спряло."],
    en: ["How many minutes ago the last inverter record is.", "Now − time of the last frame.", "Below 15 min; above 45 min means something has stopped."],
  },
  "Невъзможни стойности": {
    bg: ["Записи с физически невъзможни стойности, които не се ползват в сметките.", "SOC извън 0–100%, отрицателен товар или PV над 200 W нощем.", "0."],
    en: ["Records with physically impossible values, left out of the calculations.", "SOC outside 0–100%, negative load or PV above 200 W at night.", "0."],
  },

  // ------------------------------------------------------------ Настройки
  "Фактури": {
    bg: ["Колко фактури на Електрохолд са въведени и за какъв период.", "", "Без пропуски между периодите."],
    en: ["How many Electrohold invoices are entered and for which period.", "", "No gaps between the periods."],
  },
  "Средна сметка преди PV": {
    bg: ["Средната месечна сметка преди системата.", "По последните фактури преди пускането.", ""],
    en: ["Average monthly bill before the system.", "From the last invoices before commissioning.", ""],
  },
  "Средна сметка с PV": {
    bg: ["Средната месечна сметка след пускането на системата.", "По фактурите след пускането.", ""],
    en: ["Average monthly bill after the system was commissioned.", "From the invoices after commissioning.", ""],
  },
  "Липсващи периоди": {
    bg: ["Периоди без фактура между първата и последната.", "", "0; липсващите се въвеждат от бутона „Въведи“."],
    en: ["Periods without an invoice between the first and the last one.", "", "0; missing ones are entered with the “Enter” button."],
  },
};
