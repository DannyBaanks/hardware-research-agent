// IHRA deterministic CPU benchmark (from REA-CPU-02).
// Build: gcc -O2 -march=native -fopenmp -o cpubench cpubench.c Usage: cpubench <int|fma|triad> <threads>
// Prints one JSON line with rate and a result hash (FNV-1a over per-thread results, fixed order).
#define _GNU_SOURCE
#include <immintrin.h>
#include <omp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec*1e-9;}
static uint64_t fnv(uint64_t h,const void*p,size_t n){const uint8_t*b=p;for(size_t i=0;i<n;i++){h^=b[i];h*=1099511628211ULL;}return h;}
int main(int argc,char**argv){
  if(argc<3){fprintf(stderr,"usage\n");return 2;}
  const char*mode=argv[1]; int T=atoi(argv[2]); omp_set_num_threads(T);
  uint64_t res[64]={0}; double ops=0,t0,t1;
  if(!strcmp(mode,"int")){
    const uint64_t N=1000000000ULL; ops=(double)N*T; t0=now();
    #pragma omp parallel
    { int id=omp_get_thread_num(); uint64_t s=0x9E3779B97F4A7C15ULL*(id+1),a=0;
      for(uint64_t i=0;i<N;i++){ s^=s<<13; s^=s>>7; s^=s<<17; a+=(s*0xD6E8FEB86659FD93ULL)>>(s&31); }
      res[id]=a^s; }
    t1=now();
  } else if(!strcmp(mode,"fma")){
#ifdef __AVX512F__
    typedef __m512d V; enum{W=8};
#define VSET _mm512_set1_pd
#define VFMA _mm512_fmadd_pd
#define VADD _mm512_add_pd
#define VSTORE _mm512_storeu_pd
#else
    typedef __m256d V; enum{W=4};
#define VSET _mm256_set1_pd
#define VFMA _mm256_fmadd_pd
#define VADD _mm256_add_pd
#define VSTORE _mm256_storeu_pd
#endif
    const long N=600000000L; // 8 independent vector chains (different seeds so the compiler cannot merge them)
    ops=(double)N*8*W*2*T; t0=now();
    #pragma omp parallel
    { int id=omp_get_thread_num(); V m=VSET(0.999999), c=VSET(1e-7*(id+1));
      V a0=VSET(1.0),a1=VSET(1.1),a2=VSET(1.2),a3=VSET(1.3),a4=VSET(1.4),a5=VSET(1.5),a6=VSET(1.6),a7=VSET(1.7);
      for(long i=0;i<N;i++){ a0=VFMA(a0,m,c);a1=VFMA(a1,m,c);a2=VFMA(a2,m,c);a3=VFMA(a3,m,c);
        a4=VFMA(a4,m,c);a5=VFMA(a5,m,c);a6=VFMA(a6,m,c);a7=VFMA(a7,m,c); }
      V s=VADD(VADD(VADD(a0,a1),VADD(a2,a3)),VADD(VADD(a4,a5),VADD(a6,a7)));
      double o[W]; VSTORE(o,s); res[id]=fnv(1469598103934665603ULL,o,sizeof o); }
    t1=now();
  } else if(!strcmp(mode,"triad")){
    const size_t n=1<<25; const int R=60; // 3 x 256 MiB
    double*a=aligned_alloc(64,n*8),*b=aligned_alloc(64,n*8),*c=aligned_alloc(64,n*8);
    #pragma omp parallel for schedule(static)
    for(size_t i=0;i<n;i++){a[i]=0;b[i]=(double)(i%1000);c[i]=(double)(i%7);}
    ops=(double)n*R*24; t0=now(); // bytes moved per element: read b,c + write a
    for(int r=0;r<R;r++){
      #pragma omp parallel for schedule(static)
      for(size_t i=0;i<n;i++) a[i]=b[i]+3.0*c[i];
    }
    t1=now(); double sum=0; for(size_t i=0;i<n;i+=4096) sum+=a[i]; res[0]=fnv(1469598103934665603ULL,&sum,8); T=1;
  } else {fprintf(stderr,"bad mode\n");return 2;}
  uint64_t h=1469598103934665603ULL; for(int i=0;i<T;i++) h=fnv(h,&res[i],8);
  const char*unit=!strcmp(mode,"triad")?"GB_s":!strcmp(mode,"fma")?"GFLOP_s":"Gops_s";
  printf("{\"mode\":\"%s\",\"vector_bits\":%d,\"threads\":%d,\"seconds\":%.4f,\"%s\":%.3f,\"hash\":\"%016llx\"}\n",mode,
#ifdef __AVX512F__
    512,
#else
    256,
#endif
    atoi(argv[2]),t1-t0,unit,ops/(t1-t0)/1e9,(unsigned long long)h);
  return 0;}
