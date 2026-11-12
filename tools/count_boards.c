/* count_boards.c — exact count of every possible Squardle board.

   A board = 5 row words + 5 column words, all from the word list, all 10 different.

   Algorithm: depth-first search, one row at a time. Each column is tracked as a node in a
   prefix trie of the word list, so after k rows we know exactly which letters can extend each
   column. Candidate words for the next row are found by AND-ing five precomputed bitsets
   (one per column: "words whose letter at this position can extend this column's prefix").
   At depth 5 every column is a complete word; we then check that all 10 words are different.

   Build:  cc -O3 -o count_boards tools/count_boards.c
   Run:    ./count_boards backend/answers.txt              -> prints the totals
           ./count_boards backend/answers.txt --list       -> also prints every board, one per line
           ./count_boards words.txt 0 500                  -> only first-row words [0,500), to split
                                                              big lists across several processes */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define MAXW 20000
static char W[MAXW][6]; static int NW, NB;          /* words, bitset length in uint64 */
static int child[200000][26], wordAt[200000], NN = 1;  /* trie; wordAt = word id at depth-5 node */
static uint64_t *B;                                  /* B[(node*5+c)*NB] : words whose letter c is a child of node */

static int newnode(void){ memset(child[NN], -1, sizeof child[NN]); wordAt[NN] = -1; return NN++; }

static uint64_t leaves, boards;   /* leaves = grids with all rows/cols words; boards = also 10 distinct */
static int rows[5], node[6][5], listing;

static void dfs(int d){
    if (d == 5) {
        leaves++;
        int ids[10];
        for (int i = 0; i < 5; i++) { ids[i] = rows[i]; ids[5+i] = wordAt[node[5][i]]; }
        for (int i = 0; i < 10; i++) for (int j = i+1; j < 10; j++) if (ids[i] == ids[j]) return;
        boards++;
        if (listing) printf("%s %s %s %s %s\n", W[rows[0]], W[rows[1]], W[rows[2]], W[rows[3]], W[rows[4]]);
        return;
    }
    uint64_t cand[NB];
    memcpy(cand, B + (size_t)(node[d][0]*5+0)*NB, NB*8);
    for (int c = 1; c < 5; c++) { uint64_t *b = B + (size_t)(node[d][c]*5+c)*NB; for (int k = 0; k < NB; k++) cand[k] &= b[k]; }
    for (int k = 0; k < NB; k++) {
        uint64_t m = cand[k];
        while (m) {
            int w = k*64 + __builtin_ctzll(m); m &= m-1;
            rows[d] = w;
            for (int c = 0; c < 5; c++) node[d+1][c] = child[node[d][c]][W[w][c]-'a'];
            dfs(d+1);
        }
    }
}

int main(int argc, char **argv){
    if (argc < 2) { fprintf(stderr, "usage: %s <wordlist> [start end] [--list]\n", argv[0]); return 1; }
    FILE *f = fopen(argv[1], "r"); char buf[64];
    if (!f) { perror(argv[1]); return 1; }
    while (fgets(buf, sizeof buf, f)) {
        int n = strcspn(buf, "\r\n "); if (n != 5) continue; buf[5] = 0;
        int ok = 1; for (int i = 0; i < 5; i++) if (buf[i] < 'a' || buf[i] > 'z') ok = 0;
        if (ok) strcpy(W[NW++], buf);
    }
    NB = (NW + 63) / 64;
    newnode(); newnode();  /* node 0 unused, 1 = root */
    for (int w = 0; w < NW; w++) { int n = 1; for (int i = 0; i < 5; i++) { int l = W[w][i]-'a'; if (child[n][l] < 0) child[n][l] = newnode(); n = child[n][l]; } wordAt[n] = w; }
    B = calloc((size_t)NN*5*NB, 8);
    for (int n = 1; n < NN; n++) for (int c = 0; c < 5; c++) {
        uint64_t *b = B + (size_t)(n*5+c)*NB;
        for (int w = 0; w < NW; w++) if (child[n][W[w][c]-'a'] >= 0) b[w>>6] |= 1ULL << (w&63);
    }
    if (argc > 2 && strcmp(argv[argc-1], "--list") == 0) { listing = 1; argc--; }
    int lo = argc > 3 ? atoi(argv[2]) : 0, hi = argc > 3 ? atoi(argv[3]) : NW;
    for (int c = 0; c < 5; c++) node[0][c] = 1;
    for (int w = lo; w < hi && w < NW; w++) {
        rows[0] = w;
        int ok = 1; for (int c = 0; c < 5; c++) { node[1][c] = child[1][W[w][c]-'a']; if (node[1][c] < 0) ok = 0; }
        if (ok) dfs(1);
    }
    fprintf(listing ? stderr : stdout, "words=%d first_rows=[%d,%d) leaves=%llu boards=%llu\n", NW, lo, hi < NW ? hi : NW, (unsigned long long)leaves, (unsigned long long)boards);
    return 0;
}
