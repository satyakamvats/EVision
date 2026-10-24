const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

export type ApiForecast = { horizon_h:number; q10:number; q50:number; q90:number; confidence:number; confidence_label:'high'|'medium'|'low' };
export type ApiStation = { id:string; city:string; name:string; address:string; lat:number; lng:number; capacity:number; chargers:number; power_kw:number; connectors:string[]; price_inr_per_kwh:number; rating:number; distance_km:number|null; eta_min:number|null; occupancy_rate:number; available_count:number; forecast:ApiForecast; attribution:string };
export type ApiRecommendation = { candidate_id:string; station:ApiStation; delay_min:number; travel_km:number; travel_min:number; forecast:ApiForecast; wait_min:number; wait_interval_min:[number,number]; total_time_min:number; price_total_inr:number; carbon_kg:number; generalised_cost_inr:number; cost_terms:Record<string,number>; explanation:string };
export type ApiRecommendResponse = { options:Record<'FASTEST'|'BALANCED'|'GREENEST',ApiRecommendation>; data_source:string; as_of:string; carbon_source:'scenario'|'observed' };
export type ApiStationDetail = { station:ApiStation; utilisation:Record<string,number>; nearby:ApiStation[] };
export type ApiStationForecast = { station_id:string; as_of:string; history:Array<{at:string;observed?:number}>; forecast:Array<{at:string;q10?:number;q50?:number;q90?:number}> };

async function request<T>(path:string, init?:RequestInit):Promise<T>{
  const response=await fetch(`${API_BASE}${path}`,{headers:{'Content-Type':'application/json',...(init?.headers??{})},...init});
  if(!response.ok) throw new Error(`API request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export const api={
  meta:()=>request<{cities:Array<{slug:string;name:string}>;currency:string;data_source:string}>('/meta'),
  stations:(params:{asOf:string;horizon:number;origin?:{lat:number;lon:number};city?:string})=>{
    const search=new URLSearchParams({as_of:params.asOf,horizon_h:String(params.horizon)});
    if(params.origin){search.set('origin_lat',String(params.origin.lat));search.set('origin_lon',String(params.origin.lon));search.set('radius_km','25');}
    if(params.city)search.set('city',params.city);
    return request<{stations:ApiStation[]}>(`/stations?${search}`);
  },
  station:(id:string,asOf:string)=>request<ApiStationDetail>(`/stations/${id}?as_of=${encodeURIComponent(asOf)}`),
  forecast:(id:string,asOf:string)=>request<ApiStationForecast>(`/stations/${id}/forecast?as_of=${encodeURIComponent(asOf)}&hours_back=48`),
  reveal:(id:string,asOf:string)=>request<{observed_occupancy_rate:number;realised_wait_min:number;realised_regret_inr:number;evaluation_only:boolean}>(`/stations/${id}/reveal?as_of=${encodeURIComponent(asOf)}`),
  recommend:(asOf:string,origin={lat:12.9716,lon:77.5946},preferences:{energyKwh?:number;radiusKm?:number}={})=>request<ApiRecommendResponse>('/recommend',{method:'POST',body:JSON.stringify({origin:{lat:origin.lat,lon:origin.lon},as_of:asOf,energy_kwh:preferences.energyKwh??20,radius_km:preferences.radiusKm??25,max_delay_h:2,risk_kappa:.5,value_of_time_per_h:150,carbon_price_per_kg:.5,carbon_scenario:'tou_profile',strategy:'uncertainty_aware'})}),
  compare:(asOf:string,origin={lat:12.9716,lon:77.5946})=>request<{rows:Array<{strategy:string;station:ApiStation;wait_min:number;distance_km:number;regret_inr:number;status:string}>;mean_regret_inr:number;failure_rate:number}>('/recommend/compare',{method:'POST',body:JSON.stringify({origin,as_of:asOf,energy_kwh:20,radius_km:25,max_delay_h:2,risk_kappa:.5,value_of_time_per_h:150,carbon_price_per_kg:.5,carbon_scenario:'tou_profile',strategy:'uncertainty_aware'})}),
  evaluation:()=>request<{runs:Array<{id:string;mean_regret_inr:number;coverage:number}>}>('/evaluation/runs'),
  evaluationRun:(id:string)=>request<Record<string,unknown>>(`/evaluation/runs/${encodeURIComponent(id)}`),
  insights:(city?:string)=>request<{stations_analysed:number;load_balance_index:number;peak_utilisation:number;saturation_share:number;ranking:ApiStation[]}>(`/insights/utilisation${city?`?city=${encodeURIComponent(city)}`:''}`),
};
