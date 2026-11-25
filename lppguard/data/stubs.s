/* FuriPhone modem LPP patch stubs — nanoMIPS assembly
 *
 * Linked at fixed virtual addresses inside the freed code of
 * lpp_tst_inject_string_hdlr (VA 0x90d0ff6e, 0x434 = 1076 bytes, DHL
 * test-injection handler that is unreachable from the AP in this build).
 *
 *   STUB1 @ 0x90d0ff6e  : normalise a LCSP GNSS POS_CNF message (AGNSS)
 *   STUB2 @ 0x90d0ff86  : force the ASN.1 locationEstimate fields (AGNSS)
 *   STUB3 @ 0x90d0ffbc  : lpp_get_pos_method_support_status -> only AGNSS
 *   STUB4 @ 0x90d0ffc8  : lbs_cp_pos_method_capability_query -> only AGNSS
 *   STUB5 @ 0x90d0ffce  : non-AGNSS request -> provideLocationInformation
 *                         with locationError (stock "not supported" path)
 *
 * Las direcciones son las que salen de `nm` para las coordenadas actuales
 * (lat 0 / lon 0 / alt 0, immediatos cortos). Los .set de abajo son solo
 * documentales: el linker coloca los stubs en secuencia desde STUB1, y
 * patch_modem.py resuelve las direcciones reales con nm (los `li` de
 * nanoMIPS son de longitud variable: si cambia el tamano de un stub, todo
 * lo que va detras se desplaza). Presupuesto: 1076 B en total, ~120 B usados.
 */
    .set    noreorder
    .include "coords.inc"

    .set    STUB1, 0x90d0ff6e
    .set    STUB2, 0x90d0ff86
    .set    STUB3, 0x90d0ffbc
    .set    STUB4, 0x90d0ffc8
    .set    STUB5, 0x90d0ffce

    /* --- original code we jump back into --- */
    .set    S1_RET,  0x90d0fc28          /* after the patched move.balc */
    .set    S1_HANDLER, 0x90d14942       /* lpp_loc_agnss_process_pos_cnf_hdlr */
    .set    S2_RET,  0x90d135ae          /* after the patched li a2,... (skip trace args) */
    .set    S3_RET,  0x90d0aa1c          /* (unused: stub3 returns directly) */
    .set    S5_NOT_SUPPORT, 0x90d0e64e   /* lpp_process_requested_method_not_support_location_info_hdlr */
    .set    S5_TAIL, 0x90d0ebea          /* stock tail after that call */
    .set    S5_CONT, 0x90d0ebdc          /* resume lpp_proceed_location_info_proc_hdlr */

    .text

/* ------------------------------------------------------------------ */
/* STUB1: a0 = s0 (lcsp_gnss_pos_cnf_struct*), a1 = transaction inst.  */
/* Force resultStatus = GNSS_LOC_RESULT_NO_ERROR (1), type = POS_CNF   */
/* (0) and gnssMethod = 1 (GPS) before the AGNSS handler looks at it.  */
/* ------------------------------------------------------------------ */
    .globl stub1
stub1:
    move    $a0, $s0
    li      $a3, 1
    sb      $a3, 6($s0)                  /* resultStatus = NO_ERROR   */
    sb      $zero, 7($s0)                /* type = GNSS_POS_RESULT_TYPE_POS_CNF */
    sh      $a3, 8($s0)                  /* posResult.gnssMethod = 1 (GPS) */
    balc    S1_HANDLER
    bc      S1_RET

/* ------------------------------------------------------------------ */
/* STUB2: s0 = destination ASN.1 locationEstimate struct, s1 = source. */
/* Write fixed coordinates, overriding whatever the GNSS engine gave.  */
/* ------------------------------------------------------------------ */
    .globl stub2
stub2:
    li      $a3, 6
    sb      $a3, 0($s0)                  /* (as the original: kind = 6) */
    sb      $zero, 4($s0)                /* signOfLatitude  = NORTH     */
    li      $a3, LAT_RAW
    sw      $a3, 8($s0)                  /* degreesLatitude  (23 bit)    */
    li      $a3, LON_RAW
    sw      $a3, 12($s0)                 /* degreesLongitude (24 bit)    */
    sb      $zero, 16($s0)               /* signOfAltitude  = HEIGHT     */
    li      $a3, ALT_M
    sh      $a3, 18($s0)                 /* altitude (m)                 */
    li      $a3, UNC_K
    sb      $a3, 20($s0)                 /* uncertaintySemiMajor K       */
    sb      $a3, 21($s0)                 /* uncertaintySemiMinor K       */
    sb      $zero, 22($s0)               /* orientationMajorAxis         */
    li      $a3, UNCALT_K
    sb      $a3, 23($s0)                 /* uncertaintyAltitude K        */
    li      $a3, CONF
    sb      $a3, 24($s0)                 /* confidence (%)               */
    .hword 0x1f14        /* restore.jrc 16,ra,s0-s2 */

/* ------------------------------------------------------------------ */
/* STUB3: a0 = positioning method bit (1=AGNSS, 2=OTDOA, 4=ECID).      */
/* Report AGNSS as supported and every other method as unsupported,    */
/* which makes LPP answer with locationError "method not supported".   */
/* ------------------------------------------------------------------ */
    .globl stub3
stub3:
    li      $a3, 1
    bnec    $a0, $a3, 1f
    li      $a0, 1
    .hword 0x1f13        /* restore.jrc 16,ra,s0-s1 */
1:
    move    $a0, $zero
    .hword 0x1f13        /* restore.jrc 16,ra,s0-s1 */

/* ------------------------------------------------------------------ */
/* STUB4: lbs_cp_pos_method_capability_query(method)                   */
/* method 0 = AGNSS (CP_SUPPORT_AGNSS 0x01) -> soportado; el resto, 0. */
/* ------------------------------------------------------------------ */
    .globl stub4
stub4:
    sltiu   $a0, $a0, 1
    .hword 0x1f25        /* restore.jrc 32,ra,s0-s3 */

/* ------------------------------------------------------------------ */
/* STUB5: entra desde lpp_proceed_location_info_proc_hdlr en 0x90d0ebd8*/
/* (justo donde el firmware agrega que metodos se atendieron).         */
/*   s0 = transaction instance, s0+88 = bitmap de metodos pedidos      */
/*   bit0 = A-GNSS, bit1 = OTDOA, bit2 = ECID, bit3 = EPDU/LPPe        */
/* Si la peticion NO pide A-GNSS pero pide cualquier otro metodo, se   */
/* contesta con provideLocationInformation + locationError usando el   */
/* camino nativo "requested method not supported" (que hoy solo se     */
/* toma cuando fallan TODOS los metodos y que, con los stubs 3/4,      */
/* nunca llegaba a ejecutarse porque el metodo quedaba deshabilitado   */
/* antes, sin enviar nada). Si hay A-GNSS se sigue el camino original. */
/* ------------------------------------------------------------------ */
    .globl stub5
stub5:
    lbu     $a3, 88($s0)                 /* bitmap de metodos pedidos    */
    andi    $a3, $a3, 0xff
    andi    $a2, $a3, 0x1                /* A-GNSS pedido?               */
    bnezc   $a2, 1f                      /* si -> comportamiento nativo  */
    andi    $a2, $a3, 0xfe               /* algun otro metodo?           */
    beqzc   $a2, 1f                      /* ninguno -> nativo            */
    li      $a1, LOC_ERR_CAUSE           /* locationFailureCause         */
    move.balc $a0, $s0, S5_NOT_SUPPORT   /* rellena y envia el error     */
    bc      S5_TAIL                      /* cola nativa tras el envio    */
1:
    bc      S5_CONT                      /* sigue el codigo original     */
