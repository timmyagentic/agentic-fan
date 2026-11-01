// launchd launches this sealed, native member of the approved app. Never run a
// Python runtime or spawn root children directly from a user-writable bundle.
// The worker's signed bytes are pinned when building this executable.
#include <CommonCrypto/CommonDigest.h>
#include <mach-o/dyld.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CACHE "/private/var/run/agenticfan-service-runtime"
#ifndef WORKER_SHA256
#error Worker digest required
#endif

static int digest(int fd, int copy_to) {
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size < 1 || st.st_size > 128*1024*1024) return -1;
    CC_SHA256_CTX ctx; CC_SHA256_Init(&ctx);
    unsigned char buf[32768], sum[CC_SHA256_DIGEST_LENGTH];
    off_t total = 0; ssize_t n;
    while ((n = read(fd, buf, sizeof(buf))) > 0) {
        if ((total += n) > st.st_size) return -1;
        CC_SHA256_Update(&ctx, buf, (CC_LONG)n);
        for (ssize_t done = 0; copy_to >= 0 && done < n;) {
            ssize_t w = write(copy_to, buf+done, (size_t)(n-done));
            if (w <= 0) return -1;
            done += w;
        }
    }
    if (n < 0 || total != st.st_size) return -1;
    CC_SHA256_Final(sum, &ctx);
    char hex[65];
    for (int i=0; i<CC_SHA256_DIGEST_LENGTH; ++i) snprintf(hex+2*i,3,"%02x",sum[i]);
    return strcmp(hex,WORKER_SHA256) == 0 ? 0 : -1;
}

static int sibling(void) {
    char path[PATH_MAX], src[PATH_MAX]; uint32_t len=sizeof(path);
    if (_NSGetExecutablePath(path,&len) || !realpath(path,src)) return -1;
    char *end=strrchr(src,'/');
    if (!end || (size_t)(end-src)+sizeof("/AgenticFanControlWorker") > sizeof(src)) return -1;
    strcpy(end,"/AgenticFanControlWorker");
    return open(src,O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK);
}

int main(int argc, char **argv) {
    if (argc==2 && !strcmp(argv[1],"--verify-worker")) {
        int fd=sibling(); return fd < 0 || digest(fd,-1) != 0;
    }
    if (argc!=1 || geteuid()!=0) return 4;
    umask(077);
    if (mkdir(CACHE,0700) && errno!=EEXIST) return 5;
    struct stat st;
    if (lstat(CACHE,&st) || !S_ISDIR(st.st_mode) || st.st_uid!=0 || (st.st_mode&077)) return 4;
    char exe[]=CACHE "/worker-" WORKER_SHA256;
    int fd=open(exe,O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK);
    int valid=fd>=0 && !fstat(fd,&st) && st.st_uid==0 && (st.st_mode&0777)==0700 && digest(fd,-1)==0;
    if (fd>=0) close(fd);
    if (!valid) {
        int src=sibling(); if (src<0) return 5;
        char tmp[]=CACHE "/.stage-XXXXXXXX";
        int stage=mkstemp(tmp); if (stage<0) return 5;
        int ok=digest(src,stage)==0 && fchmod(stage,0700)==0 && fsync(stage)==0;
        close(src); if (close(stage)) ok=0;
        if (!ok || rename(tmp,exe)) { unlink(tmp); return 5; }
    }
    if (chdir("/")) return 5;
    char *args[]={exe,"--internal-helper",NULL};
    char *env[]={"PATH=/usr/bin:/bin:/usr/sbin:/sbin","HOME=/var/root","LANG=en_US.UTF-8",
                 "TMPDIR=" CACHE,"PYINSTALLER_RESET_ENVIRONMENT=1",NULL};
    execve(exe,args,env);
    return 5;
}
