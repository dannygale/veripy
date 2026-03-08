/* Bubblesort: sort 16 descending values in-place.  Memory-intensive.
 * Result: arr[0] (return value) = 1.
 */

#define N 16

static int arr[N];

int main(void) {
    /* Initialize with descending values */
    for (int i = 0; i < N; i++)
        arr[i] = N - i;

    /* Bubble sort */
    for (;;) {
        int swapped = 0;
        for (int i = 0; i < N - 1; i++) {
            if (arr[i] > arr[i + 1]) {
                int tmp = arr[i];
                arr[i] = arr[i + 1];
                arr[i + 1] = tmp;
                swapped = 1;
            }
        }
        if (!swapped) break;
    }
    return arr[0];
}
