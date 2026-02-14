#include <stdio.h>
#include <stdlib.h>
#include <windows.h>

int main() {
    // กำหนดหัวข้อหน้าต่าง Console
    SetConsoleTitle("eBay Finder Bot - C Wrapper");

    printf("===========================================\n");
    printf("   EBAY BOT - EXECUTABLE RUNNER (C)\n");
    printf("===========================================\n");
    printf("Status: Checking for Python environment...\n");

    // คำสั่งรันไฟล์ Python (สมมติว่าไฟล์ชื่อ ebay_bot.py)
    int result = system("python ebay_bot.py");

    if (result != 0) {
        printf("\n[ERROR] Could not run Python script.\n");
        printf("Make sure Python is installed and 'ebay_bot.py' exists.\n");
        printf("Press Enter to exit...");
        getchar();
    }

    return 0;
}