const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';
import {supabase} from './supabase';

export type ApiForecast = { horizon_h:number; q10:number; q50:number; q90:number; confidence:number; confidence_label:'high'|'medium'|'low' };
export type ApiStation = { id:string; city:string; name:string; address:string; lat:number; lng:number; capacity:number; chargers:number; power_kw:number; connectors:string[]; price_inr_per_kwh:number; rating:number; distance_km:number|null; eta_min:number|null; route_source?:string; occupancy_rate:number; available_count:number; forecast:ApiForecast; attribution:string };
export type ApiRecommendation = { candidate_id:string; station:ApiStation; delay_min:number; travel_km:number; travel_min:number; forecast:ApiForecast; wait_min:number; wait_interval_min:[number,number]; total_time_min:number; price_total_inr:number; carbon_kg:number; generalised_cost_inr:number; cost_terms:Record<string,number>; explanation:string; route_source?:string; vs_nearest?:Record<string,number>; queue:{c:number;tau_h:number;lambda_per_h:number;rho:number;p_wait:number;saturated:boolean} };
export type ApiRecommendResponse = { options:Partial<Record<'FASTEST'|'BALANCED'|'GREENEST',ApiRecommendation>>; data_source:string; as_of:string; carbon_source:'scenario'|'observed'; dominated?:Array<Record<string,number|string>>; reason?:string };
export type ApiStationDetail = { data_source?:string; station:ApiStation; utilisation:Record<string,number>; nearby:ApiStation[] };
export type ApiStationForecast = { station_id:string; as_of:string; data_source?:string; model_version?:string; history:Array<{at:string;observed?:number}>; forecast:Array<{at:string;q10?:number;q50?:number;q90?:number}> };
export type ApiPreferences = {energy_kwh:number;radius_km:number;risk_kappa:number;value_of_time_per_h:number;carbon_price_per_kg:number;carbon_scenario:'flat'|'tou_profile'|'low'|'high';origin_lat?:number;origin_lon?:number};
export type ApiProfile = {id:string;email?:string|null;display_name?:string|null};

async function request<T>(path:string, init?:RequestInit):Promise<T>{
  const session=supabase?(await supabase.auth.getSession()).data.session:null;
  const response=await fetch(`${API_BASE}${path}`,{headers:{'Content-Type':'application/json',...(session?{Authorization:`Bearer ${session.access_token}`}:{ }),...(init?.headers??{})},...init});
  if(!response.ok){
    let body:{error?:{code?:string;message?:string;details?:unknown}}|undefined;
    try{body=await response.json();}catch{/* Preserve the HTTP status when no JSON error body exists. */}
    const error=Object.assign(new Error(body?.error?.message??`API request failed (${response.status})`),{code:body?.error?.code,status:response.status,details:body?.error?.details});
    throw error;
  }
  return response.json() as Promise<T>;
}

export const api={
  meta:()=>request<{cities:Array<{slug:string;name:string}>;currency:string;data_source:string;mode:string;horizons:number[];model_version:string}>('/meta'),
  providers:()=>request<Record<string,unknown>>('/providers/status'),
  datasets:()=>request<Record<string,unknown>>('/datasets/status'),
  models:()=>request<Record<string,unknown>>('/models/status'),
  stations:(params:{asOf:string;horizon:number;origin?:{lat:number;lon:number};city?:string;radiusKm?:number})=>{
    const search=new URLSearchParams({as_of:params.asOf,horizon_h:String(params.horizon)});
    if(params.origin){search.set('origin_lat',String(params.origin.lat));search.set('origin_lon',String(params.origin.lon));search.set('radius_km',String(params.radiusKm??25));}
    if(params.city)search.set('city',params.city);
    return request<{stations:ApiStation[]}>(`/stations?${search}`);
  },
  station:(id:string,asOf:string)=>request<ApiStationDetail>(`/stations/${id}?as_of=${encodeURIComponent(asOf)}`),
  forecast:(id:string,asOf:string)=>request<ApiStationForecast>(`/stations/${id}/forecast?as_of=${encodeURIComponent(asOf)}&hours_back=48`),
  reveal:(id:string,asOf:string)=>request<{data_source?:string;observed_occupancy_rate:number;realised_wait_min:number;realised_regret_inr:number;evaluation_only:boolean}>(`/stations/${id}/reveal?as_of=${encodeURIComponent(asOf)}`),
  recommend:(asOf:string,origin={lat:12.9716,lon:77.5946},preferences:{energyKwh?:number;radiusKm?:number;riskKappa?:number;maxDelayH?:number;forecastHorizonH?:number}={})=>request<ApiRecommendResponse>('/recommend',{method:'POST',body:JSON.stringify({origin:{lat:origin.lat,lon:origin.lon},as_of:asOf,forecast_horizon_h:preferences.forecastHorizonH??1,energy_kwh:preferences.energyKwh??20,radius_km:preferences.radiusKm??25,max_delay_h:preferences.maxDelayH??2,risk_kappa:preferences.riskKappa??.5,value_of_time_per_h:150,carbon_price_per_kg:.5,carbon_scenario:'tou_profile',strategy:'uncertainty_aware'})}),
  route:(origin:{lat:number;lon:number},destination:{lat:number;lon:number})=>request<{distance_km:number;duration_min:number;source:string;data_source?:string;geometry?:Array<[number,number]>}>('/route',{method:'POST',body:JSON.stringify({origin,destination})}),
  geocode:(query:string)=>request<{provider:string;results:Array<{display_name:string;lat:number;lon:number}>}>(`/geocode?q=${encodeURIComponent(query)}`),
  compare:(asOf:string,origin={lat:12.9716,lon:77.5946})=>request<{data_source?:string;rows:Array<{strategy:string;station:ApiStation;wait_min:number;distance_km:number;regret_inr:number;status:string}>;mean_regret_inr:number;failure_rate:number}>('/recommend/compare',{method:'POST',body:JSON.stringify({origin,as_of:asOf,energy_kwh:20,radius_km:25,max_delay_h:2,risk_kappa:.5,value_of_time_per_h:150,carbon_price_per_kg:.5,carbon_scenario:'tou_profile',strategy:'uncertainty_aware'})}),
  evaluation:()=>request<{runs:Array<{id:string;mean_regret_inr:number;coverage:number|null}>}>('/evaluation/runs'),
  evaluationRun:(id:string)=>request<Record<string,unknown>>(`/evaluation/runs/${encodeURIComponent(id)}`),
  insights:(city?:string)=>request<{stations_analysed:number;load_balance_index:number;peak_utilisation:number;saturation_share:number;ranking:ApiStation[]}>(`/insights/utilisation${city?`?city=${encodeURIComponent(city)}`:''}`),
  profile:()=>request<ApiProfile>('/users/me'),
  updateProfile:(display_name:string)=>request<ApiProfile>('/users/me',{method:'PATCH',body:JSON.stringify({display_name})}),
  preferences:()=>request<ApiPreferences>('/users/me/preferences'),
  updatePreferences:(preferences:Partial<ApiPreferences>)=>request<ApiPreferences>('/users/me/preferences',{method:'PATCH',body:JSON.stringify(preferences)}),
  favourites:()=>request<{station_ids:string[]}>('/users/me/favourites'),
  addFavourite:(stationId:string)=>request<{station_id:string;saved:boolean}>(`/users/me/favourites/${encodeURIComponent(stationId)}`,{method:'POST'}),
  removeFavourite:(stationId:string)=>request<{station_id:string;saved:boolean}>(`/users/me/favourites/${encodeURIComponent(stationId)}`,{method:'DELETE'}),
  removeAllFavourites:()=>request<{deleted:boolean}>('/users/me/favourites',{method:'DELETE'}),
  history:()=>request<{items:Array<{id:string;created_at:string;request:Record<string,unknown>;response:ApiRecommendResponse}>}>('/users/me/recommendation-history'),
  deleteHistory:(id:string)=>request<{deleted:boolean;id:string}>(`/users/me/recommendation-history/${encodeURIComponent(id)}`,{method:'DELETE'}),
  deleteAccount:()=>request<{deleted:boolean;user_id:string}>('/users/me',{method:'DELETE'}),
};
