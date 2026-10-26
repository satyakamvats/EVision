import {createContext, useContext, useEffect, useMemo, useState} from 'react';
import type {Session, User} from '@supabase/supabase-js';
import {supabase} from './supabase';

type AuthContextValue={session:Session|null;user:User|null;loading:boolean;configured:boolean};
const AuthContext=createContext<AuthContextValue>({session:null,user:null,loading:true,configured:Boolean(supabase)});

export function AuthProvider({children}:{children:React.ReactNode}){
  const [session,setSession]=useState<Session|null>(null); const [loading,setLoading]=useState(Boolean(supabase));
  useEffect(()=>{if(!supabase){setLoading(false);return}let mounted=true;void supabase.auth.getSession().then(({data})=>{if(mounted){setSession(data.session);setLoading(false)}});const {data}=supabase.auth.onAuthStateChange((_event,next)=>{if(mounted)setSession(next)});return()=>{mounted=false;data.subscription.unsubscribe()};},[]);
  const value=useMemo(()=>({session,user:session?.user??null,loading,configured:Boolean(supabase)}),[session,loading]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(){return useContext(AuthContext)}
