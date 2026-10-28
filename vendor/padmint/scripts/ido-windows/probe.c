typedef unsigned int u32;
typedef struct Pair { int x; float y; } Pair;
static const u32 lookup[4] = { 0x12345678U, 0x90abcdefU, 7U, 19U };
u32 rotate_mix(u32 value, int count) { return (value << count) ^ (value >> (32-count)) ^ lookup[count & 3]; }
int array_sum(const int *values, int count) { int i, sum=0; for(i=0;i<count;i++) sum += values[i] * (i+1); return sum; }
double scale(double value, double offset) { return value * 1.25 + offset; }
float vector_dot(const float *a, const float *b) { return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]; }
int callback(int (*fn)(int), int value) { return fn(value) + fn(value+1); }
void update(Pair *item, int value) { item->x = value; item->y = (float)value / 3.0f; }
int choose(int value) { switch(value) { case 0:return 11; case 3:return 22; case 8:return 33; default:return -1; } }
