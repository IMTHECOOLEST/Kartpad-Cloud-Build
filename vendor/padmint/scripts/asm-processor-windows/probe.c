GLOBAL_ASM(
.text
glabel asm_value
addiu $v0, $zero, 7
jr $ra
nop
)
int data_value = 13;
