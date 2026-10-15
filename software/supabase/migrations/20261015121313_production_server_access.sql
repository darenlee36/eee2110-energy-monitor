-- Projects with automatic Data API exposure disabled need explicit server grants.
grant usage on schema public to service_role;
grant select, insert on public.telemetry to service_role;
grant select, insert, update on public.ingestion_batches to service_role;

-- The platform's RLS event trigger is not a client-callable RPC.
do $$
begin
    if to_regprocedure('public.rls_auto_enable()') is not null then
        revoke execute on function public.rls_auto_enable() from public, anon, authenticated;
    end if;
end;
$$;
